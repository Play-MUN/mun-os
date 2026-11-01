#include "sceneclock.h"

#include <QAbstractAnimation>
#include <QCoreApplication>

#include <algorithm>

// A running animation that never ends: Qt's animation timer calls it once
// per frame of the animation driver, with the driver's time.
class SceneClock::Ticker : public QAbstractAnimation {
public:
    explicit Ticker(SceneClock *clock) : QAbstractAnimation(clock), m_clock(clock) {}

    int duration() const override { return -1; }

protected:
    void updateState(State newState, State) override
    {
        if (newState == Running)
            m_last = 0;
    }

    void updateCurrentTime(int msecs) override
    {
        // start() sets the time to 0 at once, from inside demand(): not a
        // frame, and no tick may reach a layer from there.
        if (msecs <= m_last) {
            m_last = std::min(m_last, msecs);
            return;
        }
        const int step = msecs - m_last;
        m_last = msecs;
        m_clock->advance(step / 1000.0);
    }

private:
    SceneClock *m_clock;
    int m_last = 0;
};

SceneClock *SceneClock::instance()
{
    static SceneClock *clock = [] {
        auto *c = new SceneClock;
        c->setParent(QCoreApplication::instance());
        return c;
    }();
    return clock;
}

SceneClock::SceneClock() : m_ticker(new Ticker(this))
{
    m_timer.setTimerType(Qt::PreciseTimer);
    connect(&m_timer, &QTimer::timeout, this, [this] {
        const qint64 nsecs = m_sinceTick.nsecsElapsed();
        m_sinceTick.start();
        advance(nsecs / 1e9);
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

void SceneClock::setSynced(bool synced)
{
    static bool logged = false;
    if (synced == m_synced && logged)
        return;
    logged = true;
    const bool changed = synced != m_synced;
    m_synced = synced;
    m_timer.stop();
    m_ticker->stop();
    retime();
    qInfo("mun-shell: motion: %s", synced ? "on the frames the window renders (Qt Quick's animation driver)"
                                          : "on a timer, at each layer's rate");
    if (changed)
        emit syncedChanged();
}

void SceneClock::advance(qreal seconds)
{
    m_seconds += seconds;
    m_frame += m_step;
    emit tick(m_frame);
}

void SceneClock::retime()
{
    int fastest = 0;
    for (const int rate : std::as_const(m_demands))
        fastest = std::max(fastest, rate);
    if (fastest == 0) {
        m_timer.stop();
        m_ticker->stop();
        return;
    }
    if (m_synced && fastest == kBaseRate) {
        // Every frame is a tick of the base rate.
        m_timer.stop();
        m_step = 1;
        if (m_ticker->state() != QAbstractAnimation::Running)
            m_ticker->start();
        return;
    }
    // A lower rate alone ticks on the timer, synced or not: a running
    // animation would have the window draw every frame of the display, and a
    // layer at 10 frames per second would cost 60 of them.
    m_ticker->stop();
    const int step = kBaseRate / fastest;
    if (step == m_step && m_timer.isActive())
        return;
    m_step = step;
    // Keep the count on the new step's multiples so every rate's frames
    // still fall on ticks.
    m_frame = (m_frame + step - 1) / step * step;
    if (!m_timer.isActive())
        m_sinceTick.start();
    m_timer.start(1000 * step / kBaseRate);
}
