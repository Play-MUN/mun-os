// PlaybackHealth says whether the sound device plays what it is given, at
// its rate (src/systemsounds.*): a device that is open is not yet one that
// a display hears. After every write the worker gives it the frames written
// since the device was opened and the frames the device still holds
// (snd_pcm_delay); what it has played is the difference.
//
// The device is playing well from the start of a `window` over which what it
// played advanced at `slow` to `fast` times its rate, and stays so while
// every following window does. It is not, from the sample that shows it:
// - stalled: nothing played for `gap` (writes block on a device that stops,
//   so a stall also shows as samples that stop coming: healthyFor() looks at
//   the time since the last advance);
// - slow or fast: a window outside those bounds;
// - backwards: less played than before;
// - unknown: the device could not say what it holds.
// wrote() returns the problem a sample showed (None if it showed none), for
// the journal. Times are seconds on a monotonic clock the caller gives. No Qt:
// checked on the host (tests/playbackhealth_test.cpp).
#pragma once

class PlaybackHealth {
public:
    struct Limits {
        double window = 0.5;
        double slow = 0.8;
        double fast = 1.25;
        double gap = 0.25;
    };
    enum class Problem { None, Stalled, Slow, Fast, Backwards, Unknown };

    explicit PlaybackHealth(int rate) : m_rate(rate) {}
    PlaybackHealth(int rate, Limits limits) : m_rate(rate), m_limits(limits) {}

    void opened(double now)
    {
        *this = PlaybackHealth(m_rate, m_limits);
        m_open = true;
        m_openedAt = now;
    }
    void closed() { *this = PlaybackHealth(m_rate, m_limits); }

    // After a write at `now`: `written` frames since opened(), `held` frames
    // the device still holds, or below zero when it could not say.
    Problem wrote(double now, long long written, long long held)
    {
        if (!m_open)
            return Problem::None;
        if (held < 0) {
            unwell();
            m_anchorAt = -1;
            return Problem::Unknown;
        }
        const long long played = written - held;
        Problem problem = Problem::None;
        if (played < m_played) {
            unwell();
            m_played = played;
            m_anchorAt = now;
            m_anchorPlayed = played;
            return Problem::Backwards;
        }
        if (played > m_played) {
            if (m_advancingSince < 0) {
                m_advancingSince = now;
                m_anchorAt = now;
                m_anchorPlayed = played;
            } else if (now - m_lastAdvanceAt > m_limits.gap) {
                // It stopped and goes on: measured afresh from here.
                if (m_healthySince >= 0 || !m_stallSaid)
                    problem = Problem::Stalled;
                unwell();
                m_anchorAt = now;
                m_anchorPlayed = played;
            }
            m_played = played;
            m_lastAdvanceAt = now;
            m_stallSaid = false;
        } else if (m_advancingSince >= 0 && now - m_lastAdvanceAt > m_limits.gap && !m_stallSaid) {
            unwell();
            m_stallSaid = true;
            return Problem::Stalled;
        }
        if (m_anchorAt < 0) {
            m_anchorAt = now;
            m_anchorPlayed = played;
        } else if (m_advancingSince >= 0 && now - m_anchorAt >= m_limits.window) {
            m_lastRate = double(played - m_anchorPlayed) / ((now - m_anchorAt) * m_rate);
            if (m_lastRate < m_limits.slow || m_lastRate > m_limits.fast) {
                unwell();
                problem = m_lastRate < m_limits.slow ? Problem::Slow : Problem::Fast;
            } else if (m_healthySince < 0) {
                m_healthySince = m_anchorAt;
            }
            m_anchorAt = now;
            m_anchorPlayed = played;
        }
        return problem;
    }

    // Seconds it has been playing well at `now`; below zero when it is not
    // (closed, not started, since a problem, or nothing played for `gap`).
    double healthyFor(double now) const
    {
        if (!m_open || m_healthySince < 0 || now - m_lastAdvanceAt > m_limits.gap)
            return -1;
        return now - m_healthySince;
    }
    bool open() const { return m_open; }
    double openedAt() const { return m_openedAt; }
    // When it first played anything; below zero before.
    double advancingSince() const { return m_advancingSince; }
    double healthySince() const { return m_healthySince; }
    double lastAdvanceAt() const { return m_lastAdvanceAt; }
    // Its rate over the last whole window, in times its own rate.
    double lastRate() const { return m_lastRate; }
    double gap() const { return m_limits.gap; }

private:
    void unwell() { m_healthySince = -1; }

    int m_rate;
    Limits m_limits;
    bool m_open = false;
    bool m_stallSaid = false;
    double m_openedAt = -1;
    double m_advancingSince = -1;
    double m_healthySince = -1;
    double m_lastAdvanceAt = -1;
    double m_anchorAt = -1;
    long long m_anchorPlayed = 0;
    long long m_played = 0;
    double m_lastRate = 0;
};
