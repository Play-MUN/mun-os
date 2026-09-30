// CardClient mirrors the card service's state for QML. It speaks the small
// newline-delimited JSON contract of services/mun-cardd/README.md over the local UNIX socket,
// reconnects on its own, and never touches devices or mounts: everything it
// knows arrived from mun-cardd. `readerAvailable` is false while the
// socket is down, which the UI must show differently from "no card".
// A card's `shape` (its MUN Shape record, docs/shape.md) arrives in its
// record and, when only it changes, in a message of its own.
//
// `arrival` tells an insertion that arrived while the shell was watching
// from one it found: the active card's insertion if that card was not the
// active one in this connection's snapshot (it was inserted, or took over
// from another card, since), empty otherwise. A card found active at start,
// after a game or after a reconnection is not an arrival, however long its
// check or its MUN Shape copy takes afterwards.
#pragma once

#include <QLocalSocket>
#include <QObject>
#include <QSet>
#include <QTimer>
#include <QVariantMap>
#include <QtQml/qqmlregistration.h>

class CardClient : public QObject {
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool readerAvailable READ readerAvailable NOTIFY changed)
    // absent | reading | valid | invalid  (state of the active card, or absent)
    Q_PROPERTY(QString state READ state NOTIFY changed)
    Q_PROPERTY(QVariantMap card READ card NOTIFY changed)        // active card record from the service
    Q_PROPERTY(QVariantMap info READ info NOTIFY changed)        // validated manifest, empty unless valid
    Q_PROPERTY(QVariantMap error READ error NOTIFY changed)      // {code,message,detail}, empty unless invalid
    Q_PROPERTY(QString coverUrl READ coverUrl NOTIFY changed)
    Q_PROPERTY(int waitingCount READ waitingCount NOTIFY changed)
    Q_PROPERTY(QString arrival READ arrival NOTIFY changed)
    Q_PROPERTY(QString socketPath READ socketPath CONSTANT)

public:
    explicit CardClient(QObject *parent = nullptr);
    ~CardClient() override;

    bool readerAvailable() const { return m_socket.state() == QLocalSocket::ConnectedState && m_snapshotSeen; }
    QString state() const;
    QVariantMap card() const { return m_active; }
    QVariantMap info() const { return m_active.value("info").toMap(); }
    QVariantMap error() const { return m_active.value("error").toMap(); }
    QString coverUrl() const;
    int waitingCount() const;
    QString arrival() const;
    QString socketPath() const { return m_path; }

signals:
    void changed();
    // Emitted when the card the UI was looking at disappears, so screens can leave.
    void activeCardRemoved(const QString &slot);

private:
    void connectToService();
    void readLines();
    void handleMessage(const QJsonObject &message);
    void recomputeActive();

    QString m_path;
    QLocalSocket m_socket;
    QTimer m_reconnect;
    QByteArray m_buffer;
    bool m_snapshotSeen = false;
    QList<QVariantMap> m_cards;   // in service order
    QSet<QString> m_found;        // the insertions active in this connection's snapshot
    QVariantMap m_active;
};
