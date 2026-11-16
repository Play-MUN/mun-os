// StartupClock is the start-up's clock (qml/BootLayer.qml, through
// src/bootclock.*): the picture keeps time with the start-up sound while the
// sound is a clock it can trust, and goes on alone, at real speed, from where
// it is, once it is not.
//
// While it follows the sound, `t` is what has been heard of it
// (SystemSounds::heard): below zero while the first samples wait in the
// device (the screen black), held while the sound stalls (up to stallWait),
// and following it again when it goes on. The sound stops being trusted,
// for the rest of the start-up, when:
// - it has not started within startWait of begin();
// - it does not advance from its start within startWait of its first
//   samples (a device that takes samples and plays none);
// - it stalls past stallWait;
// - it goes back by more than `backwards`;
// - over `window` seconds of advancing it runs slower than `slow` or faster
//   than `fast` times real time (a device playing at a wrong rate: the
//   picture would crawl or race); a stall (no advance for `gap`) starts the
//   measure afresh, so a held picture is not taken for a slow one;
// - the device recovered `recoveries` times since begin();
// - it is over (played out or dropped) while the picture still runs.
// From then on `t` runs on its own from where it was, at real speed: it
// never goes back, starts again, or follows the sound again. Only a sound
// that had not started in time, and starts while the screen is still black
// (t below lateFollow), is followed again (started()).
//
// Every change of clock leaves a line for the journal (takeNote()). Times are
// seconds on a monotonic clock the caller gives. No Qt: checked on the host
// (tests/startupclock_test.cpp).
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <string>

class StartupClock {
public:
    struct Limits {
        double startWait = 3.0;
        double stallWait = 2.0;
        double backwards = 0.05;
        double window = 1.0;
        double slow = 0.8;
        double fast = 1.25;
        double gap = 0.2;
        int recoveries = 2;
        double lateFollow = 0.25;
    };

    StartupClock() = default;
    explicit StartupClock(Limits limits) : m_limits(limits) {}

    // The start-up begins at `now`: following its sound, or alone from 0
    // (no sound will play). `recoveries` is the device's count so far.
    void begin(double now, bool withSound, int recoveries = 0)
    {
        *this = StartupClock(m_limits);
        m_begun = now;
        m_recoveriesAtBegin = recoveries;
        if (!withSound)
            alone(now, 0, "the interface sounds are off", false);
    }

    // One frame at `now`: `heard` seconds of the sound so far (NaN while it
    // is not playing: not started yet, or over), `recoveries` the device's
    // count. Returns t.
    double tick(double now, double heard, int recoveries)
    {
        if (!m_following) {
            m_t = m_aloneFrom + (now - m_aloneAt);
            return m_t;
        }
        if (std::isnan(heard)) {
            if (m_heardOnce)
                alone(now, std::max(0.0, m_t), "the sound is over (played out or dropped)", true);
            else if (now - m_begun > m_limits.startWait)
                alone(now, std::max(0.0, m_t), "no sound after " + ms(m_limits.startWait), false);
            return m_t;
        }
        if (!m_heardOnce) {
            m_heardOnce = true;
            m_firstHeardAt = m_lastHeardAt = m_anchorAt = now;
            m_lastHeard = m_anchorHeard = heard;
            note("the picture follows the sound");
        }
        if (recoveries - m_recoveriesAtBegin >= m_limits.recoveries) {
            alone(now, std::max(0.0, m_t),
                  "the sound device recovered " + std::to_string(recoveries - m_recoveriesAtBegin) + " times", true);
            return m_t;
        }
        if (heard < m_lastHeard - m_limits.backwards) {
            alone(now, std::max(0.0, m_t), "the sound went back from " + secs(m_lastHeard) + " to " + secs(heard), true);
            return m_t;
        }
        if (heard > m_lastHeard) {
            if (now - m_lastHeardAt > m_limits.gap) {
                // Going on after a stall: the rate is measured afresh.
                if (now - m_lastHeardAt > 0.5)
                    note("the sound plays again after " + std::to_string(int(std::lround((now - m_lastHeardAt) * 1000)))
                         + " ms; the picture held");
                m_anchorAt = now;
                m_anchorHeard = heard;
            }
            m_lastHeard = heard;
            m_lastHeardAt = now;
        } else if (heard <= 0 && now - m_firstHeardAt > m_limits.startWait) {
            alone(now, 0, "the sound did not advance from its start in " + ms(m_limits.startWait), true);
            return m_t;
        } else if (heard > 0 && now - m_lastHeardAt > m_limits.stallWait) {
            alone(now, std::max(0.0, heard), "the sound stalled for " + ms(m_limits.stallWait), true);
            return m_t;
        }
        if (heard > 0 && now - m_anchorAt >= m_limits.window && now - m_lastHeardAt <= m_limits.gap) {
            const double rate = (heard - m_anchorHeard) / (now - m_anchorAt);
            if (rate < m_limits.slow || rate > m_limits.fast) {
                char text[96];
                std::snprintf(text, sizeof text, "the sound runs at %.2f times real time", rate);
                alone(now, std::max(0.0, m_t), text, true);
                return m_t;
            }
            m_anchorAt = now;
            m_anchorHeard = heard;
        }
        // A picture never goes back: t only follows the sound forward.
        m_t = std::max(m_t, heard);
        return m_t;
    }

    // The sound's first samples went to the device at `now`. A sound that had
    // not started in time is followed again while the screen is still black.
    void started(double now)
    {
        (void)now;
        if (m_following || !m_mayFollowAgain || m_t >= m_limits.lateFollow)
            return;
        m_following = true;
        m_heardOnce = false;
        note("the sound started late; the picture follows it again");
    }

    // The sound will not be heard (no device).
    void dropped(double now)
    {
        if (m_following)
            alone(now, std::max(0.0, m_t), "the sound was dropped (no device)", true);
    }

    double t() const { return m_t; }
    bool following() const { return m_following; }
    // The journal's line about the last change of clock, once; empty if none.
    std::string takeNote()
    {
        std::string out;
        out.swap(m_note);
        return out;
    }

private:
    static std::string ms(double seconds) { return std::to_string(int(std::lround(seconds * 1000))) + " ms"; }
    static std::string secs(double seconds)
    {
        char text[32];
        std::snprintf(text, sizeof text, "%.2f s", seconds);
        return text;
    }
    void note(const std::string &line) { m_note = m_note.empty() ? line : m_note + "\n" + line; }
    void alone(double now, double from, const std::string &why, bool distrust)
    {
        m_following = false;
        m_mayFollowAgain = !distrust;
        m_aloneFrom = from;
        m_aloneAt = now;
        m_t = from;
        char text[48];
        std::snprintf(text, sizeof text, "%.2f", from);
        note("the picture runs on its own from " + std::string(text) + " s: " + why);
    }

    Limits m_limits;
    bool m_following = true;
    bool m_mayFollowAgain = false;
    bool m_heardOnce = false;
    int m_recoveriesAtBegin = 0;
    double m_begun = 0;
    double m_t = -1;
    double m_firstHeardAt = 0, m_lastHeardAt = 0, m_lastHeard = 0;
    double m_anchorAt = 0, m_anchorHeard = 0;
    double m_aloneFrom = 0, m_aloneAt = 0;
    std::string m_note;
};
