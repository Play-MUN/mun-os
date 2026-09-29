#include "launchclient.h"

#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>

namespace {
// Shared with mun-cardd (MAX_FRAME_BYTES) and mun-launchd: one line, cover included.
const int kMaxFrameBytes = 2 * 1024 * 1024;
const QString kResultFile = QStringLiteral("/run/mun/launch/last-result.json");
}

LaunchClient::LaunchClient(QObject *parent) : QObject(parent)
{
    m_path = qEnvironmentVariable("MUN_LAUNCHD_SOCKET", QStringLiteral("/run/mun/launchd.sock"));
    m_lastResult = readPersistedResult();

    connect(&m_socket, &QLocalSocket::readyRead, this, &LaunchClient::readLines);
    connect(&m_socket, &QLocalSocket::connected, this, [this] { m_buffer.clear(); emit changed(); });
    connect(&m_socket, &QLocalSocket::disconnected, this, [this] {
        m_snapshotSeen = false;
        m_requestPending = false;
        if (m_releasePending) {
            m_releasePending = false;
            emit releaseFinished(false, QStringLiteral("launcher_unavailable"), QStringLiteral("Se perdió la conexión con el lanzador"));
        }
        m_reconnect.start();
        emit changed();
    });
    connect(&m_socket, &QLocalSocket::errorOccurred, this, [this](QLocalSocket::LocalSocketError) {
        m_snapshotSeen = false;
        if (!m_reconnect.isActive())
            m_reconnect.start();
        emit changed();
    });
    m_reconnect.setInterval(1500);
    m_reconnect.setSingleShot(true);
    connect(&m_reconnect, &QTimer::timeout, this, &LaunchClient::connectToService);
    connectToService();
}

QVariantMap LaunchClient::readPersistedResult()
{
    QFile file(kResultFile);
    if (!file.open(QIODevice::ReadOnly))
        return {};
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll());
    return document.isObject() ? document.object().toVariantMap() : QVariantMap();
}

LaunchClient::~LaunchClient()
{
    // The socket closes itself when it is destroyed and says so
    // (disconnected); being a member, that happens after the members its
    // handlers write to are gone. Stop listening to it first.
    m_socket.disconnect(this);
    m_reconnect.stop();
}

void LaunchClient::connectToService()
{
    if (m_socket.state() != QLocalSocket::UnconnectedState)
        return;
    m_socket.connectToServer(m_path);
}

void LaunchClient::launch(const QString &slot, const QString &serial, const QString &version)
{
    // One request at a time; the UI disables the button, this is the backstop.
    if (!available() || m_requestPending || m_state != QLatin1String("idle"))
        return;
    m_requestPending = true;
    m_lastError.clear();
    emit changed();
    send({{"type", "launch"}, {"slot", slot}, {"serial", serial}, {"version", version}});
}

void LaunchClient::acknowledge()
{
    m_lastResult.insert("acknowledged", true);
    emit changed();
    if (available())
        send({{"type", "ack"}});
}

void LaunchClient::release(const QString &serial)
{
    if (m_releasePending || m_socket.state() != QLocalSocket::ConnectedState)
        return;
    m_releasePending = true;
    emit changed();
    send({{"type", "release"}, {"serial", serial}});
}

void LaunchClient::send(const QJsonObject &message)
{
    m_socket.write(QJsonDocument(message).toJson(QJsonDocument::Compact) + '\n');
    m_socket.flush();
}

void LaunchClient::readLines()
{
    m_buffer += m_socket.readAll();
    int newline;
    while ((newline = m_buffer.indexOf('\n')) >= 0) {
        const QByteArray line = m_buffer.left(newline);
        m_buffer.remove(0, newline + 1);
        const QJsonDocument document = QJsonDocument::fromJson(line);
        if (document.isObject())
            handleMessage(document.object());
    }
    if (m_buffer.size() > kMaxFrameBytes) {
        // The service never sends a longer line (a 1 MiB cover as base64 plus
        // metadata fits in the budget), so this is a protocol break: do not
        // guess where the next line starts, reconnect and take a fresh snapshot.
        qWarning("%s: unfinished line over %d bytes; reconnecting", "launchclient", kMaxFrameBytes);
        m_buffer.clear();
        m_snapshotSeen = false;
        m_requestPending = false;
        m_socket.abort();
        m_reconnect.start();
        emit changed();
    }
}

void LaunchClient::handleMessage(const QJsonObject &message)
{
    const QString type = message.value("type").toString();
    if (type == QLatin1String("snapshot") || type == QLatin1String("session")) {
        m_snapshotSeen = true;
        m_state = message.value("state").toString(QStringLiteral("idle"));
        m_session = message.value("session").toObject().toVariantMap();
        if (message.value("last_result").isObject()) {
            QVariantMap incoming = message.value("last_result").toObject().toVariantMap();
            // The launcher may have restarted before it persisted our acknowledgement;
            // do not show the same result twice, tell it again instead. A console
            // failure reported after the player saw the result (a cleanup that
            // failed after the session ended) is new: that result is shown again.
            if (incoming.value("session") == m_lastResult.value("session")
                    && m_lastResult.value("acknowledged").toBool() && !incoming.value("acknowledged").toBool()
                    && incoming.value("platform") == m_lastResult.value("platform")) {
                incoming.insert("acknowledged", true);
                send({{"type", "ack"}});
            }
            m_lastResult = incoming;
        }
        if (m_state != QLatin1String("idle"))
            m_requestPending = false;
    } else if (type == QLatin1String("released")) {
        m_releasePending = false;
        const bool ok = message.value("ok").toBool();
        const QJsonObject error = message.value("error").toObject();
        emit releaseFinished(ok, error.value("code").toString(), ok ? QString() : error.value("message").toString());
    } else if (type == QLatin1String("launch_result")) {
        m_requestPending = false;
        if (!message.value("accepted").toBool()) {
            const QJsonObject error = message.value("error").toObject();
            m_lastError = error.value("message").toString();
            emit launchRejected(error.value("code").toString(), m_lastError);
        }
    } else {
        return;
    }
    emit changed();
}
