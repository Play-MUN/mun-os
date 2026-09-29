#include "sceneclock.h"

#include <QCoreApplication>

#include <algorithm>

SceneClock *SceneClock::instance()
{
    static SceneClock *clock = [] {
        auto *c = new SceneClock;
        c->setParent(QCoreApplication::instance());
        return c;
    }();
    return clock;
}

SceneClock::SceneClock()
{
    m_timer.setTimerType(Qt::PreciseTimer);
    connect(&m_timer, &QTimer::timeout, this, [this] {
        m_frame += m_step;
        emit tick(m_frame);
    });
}

int SceneClock::allowedRate(int rate)
{
    if (rate <= 0)
        return 0;
    for (const int allowed : {5, 10, 20})
        if (rate <= allowed)
            return allowed;
    return kBaseRate;
}

bool SceneClock::due(quint64 frame, int rate)
{
    const int allowed = allowedRate(rate);
    return allowed > 0 && frame % (kBaseRate / allowed) == 0;
}

void SceneClock::demand(const QObject *who, int rate)
{
    const int allowed = allowedRate(rate);
    if (allowed == 0) {
        if (m_demands.remove(who))
            retime();
        return;
    }
    if (m_demands.value(who) == allowed)
        return;
    m_demands.insert(who, allowed);
    retime();
}

void SceneClock::retime()
{
    int fastest = 0;
    for (const int rate : std::as_const(m_demands))
        fastest = std::max(fastest, rate);
    if (fastest == 0) {
        m_timer.stop();
        return;
    }
    const int step = kBaseRate / fastest;
    if (step == m_step && m_timer.isActive())
        return;
    m_step = step;
    // Keep the count on the new step's multiples so every rate's frames
    // still fall on ticks.
    m_frame = (m_frame + step - 1) / step * step;
    m_timer.start(1000 * step / kBaseRate);
}
