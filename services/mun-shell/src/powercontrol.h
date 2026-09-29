// PowerControl is the only place the shell touches a privileged operation, and
// it does so indirectly: it runs the mun-power helper through sudo, where a
// dedicated sudoers rule allows exactly that command for the shell user. The
// shell process itself never runs as root (see deploy/mun-shell.service).
#pragma once

#include <QObject>
#include <QtQml/qqmlregistration.h>

class PowerControl : public QObject {
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool busy READ busy NOTIFY busyChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)

public:
    explicit PowerControl(QObject *parent = nullptr);

    // Requests a clean system power-off. Returns immediately; the result is
    // reported through busy/lastError because the system goes down on success.
    // lastError is technical detail in English (the helper's own output); the
    // interface words the failure itself.
    Q_INVOKABLE void powerOff();

    bool busy() const { return m_busy; }
    QString lastError() const { return m_lastError; }

signals:
    void busyChanged();
    void lastErrorChanged();

private:
    void runHelper(const QString &action);

    bool m_busy = false;
    QString m_lastError;
};
