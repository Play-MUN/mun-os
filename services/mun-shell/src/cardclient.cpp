#include "cardclient.h"

namespace {
// Shared with mun-cardd (MAX_FRAME_BYTES) and mun-launchd: one line, cover included.
const int kMaxFrameBytes = 2 * 1024 * 1024;
}

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QUrl>

CardClient::CardClient(QObject *parent) : QObject(parent)
{
    m_path = qEnvironmentVariable("MUN_CARDD_SOCKET", QStringLiteral("/run/mun/cardd.sock"));

    connect(&m_socket, &QLocalSocket::readyRead, this, &CardClient::readLines);
    connect(&m_socket, &QLocalSocket::connected, this, [this] {
        m_buffer.clear();
        emit changed();
    });
    connect(&m_socket, &QLocalSocket::disconnected, this, [this] {
        // Losing the service means losing its truth: forget cards and say so.
        m_snapshotSeen = false;
        m_cards.clear();
        recomputeActive();
        m_reconnect.start();
    });
    connect(&m_socket, &QLocalSocket::errorOccurred, this, [this](QLocalSocket::LocalSocketError) {
        m_snapshotSeen = false;
        if (!m_reconnect.isActive())
            m_reconnect.start();
        emit changed();
    });

    // Back-off is deliberately short: the shell is the only client and the
    // service restarts in about a second.
    m_reconnect.setInterval(1500);
    m_reconnect.setSingleShot(true);
    connect(&m_reconnect, &QTimer::timeout, this, &CardClient::connectToService);
    connectToService();
}

CardClient::~CardClient()
{
    // The socket closes itself when it is destroyed and says so
    // (disconnected); being a member, that happens after the members its
    // handlers write to are gone. Stop listening to it first.
    m_socket.disconnect(this);
    m_reconnect.stop();
}

void CardClient::connectToService()
{
    if (m_socket.state() != QLocalSocket::UnconnectedState)
        return;
    m_socket.connectToServer(m_path);
}

void CardClient::readLines()
{
    m_buffer += m_socket.readAll();
    int newline;
    while ((newline = m_buffer.indexOf('\n')) >= 0) {
        const QByteArray line = m_buffer.left(newline);
        m_buffer.remove(0, newline + 1);
        QJsonParseError parseError{};
        const QJsonDocument document = QJsonDocument::fromJson(line, &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject())
            continue;  // a malformed line from the service is ignored, not fatal
        handleMessage(document.object());
    }
    if (m_buffer.size() > kMaxFrameBytes) {
        // The service never sends a longer line (a 1 MiB cover as base64 plus
        // metadata fits in the budget), so this is a protocol break: do not
        // guess where the next line starts, reconnect and take a fresh snapshot.
        qWarning("%s: unfinished line over %d bytes; reconnecting", "cardclient", kMaxFrameBytes);
        m_buffer.clear();
        m_snapshotSeen = false;
        m_cards.clear();
        recomputeActive();
        m_socket.abort();
        m_reconnect.start();
        emit changed();
    }
}

void CardClient::handleMessage(const QJsonObject &message)
{
    const QString type = message.value("type").toString();
    if (type == QLatin1String("snapshot")) {
        m_cards.clear();
        for (const QJsonValue &value : message.value("cards").toArray())
            m_cards.append(value.toObject().toVariantMap());
        m_snapshotSeen = true;
    } else if (type == QLatin1String("card")) {
        const QVariantMap card = message.value("card").toObject().toVariantMap();
        const QString slot = card.value("slot").toString();
        bool replaced = false;
        for (QVariantMap &existing : m_cards) {
            if (existing.value("slot").toString() == slot) {
                existing = card;
                replaced = true;
                break;
            }
        }
        if (!replaced)
            m_cards.append(card);
    } else if (type == QLatin1String("removed")) {
        const QString slot = message.value("slot").toString();
        const bool wasActive = m_active.value("slot").toString() == slot;
        m_cards.erase(std::remove_if(m_cards.begin(), m_cards.end(), [&](const QVariantMap &card) {
            return card.value("slot").toString() == slot;
        }), m_cards.end());
        if (wasActive)
            emit activeCardRemoved(slot);
    } else {
        return;
    }
    recomputeActive();
}

void CardClient::recomputeActive()
{
    QVariantMap active;
    for (const QVariantMap &card : m_cards) {
        if (card.value("active").toBool()) {
            active = card;
            break;
        }
    }
    m_active = active;
    emit changed();
}

QString CardClient::state() const
{
    if (m_active.isEmpty())
        return QStringLiteral("absent");
    return m_active.value("state").toString();
}

QString CardClient::coverUrl() const
{
    // The service ships the PNG bytes (size-checked) inside the card record;
    // Qt Quick's Image loads data URLs directly, so no file ever touches disk.
    const QString data = m_active.value("info").toMap().value("cover_data").toString();
    return data.isEmpty() ? QString() : QStringLiteral("data:image/png;base64,") + data;
}

int CardClient::waitingCount() const
{
    int count = 0;
    for (const QVariantMap &card : m_cards)
        if (card.value("state").toString() == QLatin1String("waiting"))
            ++count;
    return count;
}
