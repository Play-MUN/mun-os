#include "powercontrol.h"

#include <QProcess>

namespace {
// Installed by deploy/stage.sh; the sudoers rule refers to this exact path.
const QString kHelper = QStringLiteral("/usr/local/libexec/mun-power");
}

PowerControl::PowerControl(QObject *parent) : QObject(parent) {}

void PowerControl::powerOff()
{
    runHelper(QStringLiteral("poweroff"));
}

void PowerControl::runHelper(const QString &action)
{
    if (m_busy)
        return;
    m_busy = true;
    m_lastError.clear();
    emit busyChanged();
    emit lastErrorChanged();

    auto *process = new QProcess(this);
    process->setProgram(QStringLiteral("sudo"));
    // -n: never prompt. If the sudoers rule is missing we want a clear failure,
    // not a hung UI waiting for a password nobody can type.
    process->setArguments({QStringLiteral("-n"), kHelper, action});
    process->setProcessChannelMode(QProcess::MergedChannels);
    connect(process, &QProcess::finished, this, [this, process, action](int code, QProcess::ExitStatus status) {
        if (status != QProcess::NormalExit || code != 0) {
            const QString output = QString::fromUtf8(process->readAll()).trimmed();
            m_lastError = QStringLiteral("mun-power %1 exited with status %2: %3").arg(action).arg(code).arg(output);
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
