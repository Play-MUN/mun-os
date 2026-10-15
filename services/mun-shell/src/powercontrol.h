// PowerControl is the only place the shell touches a privileged operation, and
// it does so indirectly: it runs the mun-power and mun-reset helpers through
// sudo, where a dedicated sudoers rule allows exactly those commands for the
// shell user (deploy/mun-shell.sudoers). The shell process itself never runs
// as root (see deploy/mun-shell.service).
#pragma once

#include <QObject>
#include <QtQml/qqmlregistration.h>

class PowerControl : public QObject {
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool busy READ busy NOTIFY busyChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)
    // What the last request was, "poweroff" or "reset", and the helper's exit
    // status when it failed (mun-reset's refusals: services/mun-reset).
    Q_PROPERTY(QString lastAction READ lastAction NOTIFY lastErrorChanged)
    Q_PROPERTY(int lastStatus READ lastStatus NOTIFY lastErrorChanged)

public:
    explicit PowerControl(QObject *parent = nullptr);

    // Requests a clean system power-off. Returns immediately; the result is
    // reported through busy/lastError because the system goes down on success.
    // lastError is technical detail in English (the helper's own output); the
    // interface words the failure itself.
    Q_INVOKABLE void powerOff();
    // Asks for a console reset (mun-reset request): on success the console
    // restarts and the reset happens at boot; a refusal (a Game Card in the
    // slot, a game running) or a failure is reported like powerOff()'s.
    Q_INVOKABLE void resetConsole();

    bool busy() const { return m_busy; }
    QString lastError() const { return m_lastError; }
    QString lastAction() const { return m_lastAction; }
    int lastStatus() const { return m_lastStatus; }

signals:
    void busyChanged();
    void lastErrorChanged();

private:
    void runHelper(const QString &helper, const QString &action, const QString &name);

    bool m_busy = false;
    QString m_lastError;
    QString m_lastAction;
    int m_lastStatus = 0;
};
