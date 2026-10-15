#include "powercontrol.h"

#include <QProcess>

namespace {
// Installed by deploy/stage.sh and services/mun-reset/deploy/stage.sh; the
// sudoers rule refers to these exact paths.
const QString kPowerHelper = QStringLiteral("/usr/local/libexec/mun-power");
const QString kResetHelper = QStringLiteral("/usr/local/libexec/mun-reset");
}

PowerControl::PowerControl(QObject *parent) : QObject(parent) {}

void PowerControl::powerOff()
{
    runHelper(kPowerHelper, QStringLiteral("poweroff"), QStringLiteral("poweroff"));
}

void PowerControl::resetConsole()
{
    runHelper(kResetHelper, QStringLiteral("request"), QStringLiteral("reset"));
}

void PowerControl::runHelper(const QString &helper, const QString &action, const QString &name)
{
    if (m_busy)
        return;
    m_busy = true;
    m_lastError.clear();
    m_lastAction = name;
    m_lastStatus = 0;
    emit busyChanged();
    emit lastErrorChanged();

    auto *process = new QProcess(this);
    process->setProgram(QStringLiteral("sudo"));
    // -n: never prompt. If the sudoers rule is missing we want a clear failure,
    // not a hung UI waiting for a password nobody can type.
    process->setArguments({QStringLiteral("-n"), helper, action});
    process->setProcessChannelMode(QProcess::MergedChannels);
    connect(process, &QProcess::finished, this, [this, process, helper, action](int code, QProcess::ExitStatus status) {
        if (status != QProcess::NormalExit || code != 0) {
            const QString output = QString::fromUtf8(process->readAll()).trimmed();
            m_lastStatus = status == QProcess::NormalExit ? code : -1;
            m_lastError = QStringLiteral("%1 %2 exited with status %3: %4")
                              .arg(helper.section(QLatin1Char('/'), -1), action).arg(code).arg(output);
            m_busy = false;
            emit lastErrorChanged();
            emit busyChanged();
        }
        // On success the machine is shutting down; stay busy so the UI keeps
        // showing the shutdown state until the process disappears.
        process->deleteLater();
    });
    connect(process, &QProcess::errorOccurred, this, [this, process](QProcess::ProcessError) {
        m_lastError = QStringLiteral("could not run sudo: %1").arg(process->errorString());
        m_busy = false;
        emit lastErrorChanged();
        emit busyChanged();
        process->deleteLater();
    });
    process->start();
}
