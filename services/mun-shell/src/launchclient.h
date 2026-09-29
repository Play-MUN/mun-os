// LaunchClient mirrors mun-launchd for QML: whether launching is possible
// right now, the session state, and the last session's result. It also reads
// the persisted result at startup so Main can skip the splash and show what
// happened while the shell was stopped. The shell never starts processes.
#pragma once

#include <QLocalSocket>
#include <QObject>
#include <QTimer>
#include <QVariantMap>
#include <QtQml/qqmlregistration.h>

class LaunchClient : public QObject {
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool available READ available NOTIFY changed)          // socket connected, snapshot seen
    Q_PROPERTY(QString state READ state NOTIFY changed)                // idle | preparing | starting | running | stopping
    Q_PROPERTY(bool requestPending READ requestPending NOTIFY changed) // we asked, no answer yet
    Q_PROPERTY(QVariantMap session READ session NOTIFY changed)
    Q_PROPERTY(QVariantMap lastResult READ lastResult NOTIFY changed)
    Q_PROPERTY(bool hasUnacknowledgedResult READ hasUnacknowledgedResult NOTIFY changed)
    Q_PROPERTY(QString lastError READ lastError NOTIFY changed)
    Q_PROPERTY(bool releasePending READ releasePending NOTIFY changed)   // safe removal asked, no answer yet

public:
    explicit LaunchClient(QObject *parent = nullptr);
    ~LaunchClient() override;

    Q_INVOKABLE void launch(const QString &slot, const QString &serial, const QString &version);
    Q_INVOKABLE void acknowledge();
    // Safe removal (docs/saves.md): the launcher refuses while a session uses the
    // card, otherwise the card service flushes and unmounts; the card then
    // shows as released and may be pulled.
    Q_INVOKABLE void release(const QString &serial);
    // Read the persisted result synchronously (startup decision: splash or not).
    static QVariantMap readPersistedResult();

    bool available() const { return m_socket.state() == QLocalSocket::ConnectedState && m_snapshotSeen; }
    QString state() const { return m_state; }
    bool requestPending() const { return m_requestPending; }
    QVariantMap session() const { return m_session; }
    QVariantMap lastResult() const { return m_lastResult; }
    bool hasUnacknowledgedResult() const { return !m_lastResult.isEmpty() && !m_lastResult.value("acknowledged").toBool(); }
    QString lastError() const { return m_lastError; }
    bool releasePending() const { return m_releasePending; }

signals:
    void changed();
    void launchRejected(const QString &code, const QString &message);
    void releaseFinished(bool ok, const QString &code, const QString &message);

private:
    void connectToService();
    void readLines();
    void handleMessage(const QJsonObject &message);
    void send(const QJsonObject &message);

    QString m_path;
    QLocalSocket m_socket;
    QTimer m_reconnect;
    QByteArray m_buffer;
    bool m_snapshotSeen = false;
    bool m_requestPending = false;
    bool m_releasePending = false;
    QString m_state = QStringLiteral("idle");
    QVariantMap m_session;
    QVariantMap m_lastResult;
    QString m_lastError;
};
