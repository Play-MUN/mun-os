#include "bootclock.h"

#include <QString>
#include <QtGlobal>

#include <chrono>

double BootClock::now()
{
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

void BootClock::report(bool wasFollowing)
{
    const std::string note = m_clock.takeNote();
    if (!note.empty()) {
        for (const QString &line : QString::fromStdString(note).split(QLatin1Char('\n')))
            qInfo("mun-shell: start-up: %s", qPrintable(line));
    }
    if (wasFollowing != m_clock.following())
        emit changed();
}

void BootClock::begin(bool withSound, int recoveries)
{
    m_clock.begin(now(), withSound, recoveries);
    report(m_clock.following());
    emit changed();
}

double BootClock::tick(double heard, int recoveries)
{
    const bool was = m_clock.following();
    const double t = m_clock.tick(now(), heard, recoveries);
    report(was);
    return t;
}

void BootClock::started()
{
    const bool was = m_clock.following();
    m_clock.started(now());
    report(was);
}

void BootClock::dropped()
{
    const bool was = m_clock.following();
    m_clock.dropped(now());
    report(was);
}
