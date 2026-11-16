// MotionClock is Shape motion's clock (src/motionscene.*): it turns the time
// between two display frames into fixed simulation steps of `step` seconds,
// and says where between the last two steps the frame is shown.
//
// The scene is simulated only in whole steps (the same events at the same
// steps give the same scene on any console); a frame shows it `alpha` of
// the way from the step before the last to the last (0..1), so what moves
// advances by exactly the time that passed, a step behind at most. Steps
// are taken when a whole step's time has gathered: the time left over is
// always less than a step, which is what makes the shown time advance
// evenly at any display rate. At most `maxSteps` are taken at once; whole
// steps beyond that (the console fell behind) are dropped and counted.
//
// No Qt: the arithmetic is checked on the host (tests/motionclock_test.cpp).
#pragma once

#include <cmath>

class MotionClock {
public:
    struct Advance {
        int steps = 0;       // steps to simulate now, in order
        int dropped = 0;     // whole steps given up (fell behind)
        double alpha = 0;    // where the frame is between the last two steps (0..1)
    };

    explicit MotionClock(double step, int maxSteps = 3) : m_step(step), m_maxSteps(maxSteps) {}

    // `elapsed` seconds since the last frame (never negative).
    Advance advance(double elapsed)
    {
        Advance out;
        m_pending += elapsed > 0 ? elapsed : 0;
        while (m_pending >= m_step && out.steps < m_maxSteps) {
            m_pending -= m_step;
            ++out.steps;
        }
        if (m_pending >= m_step) {
            out.dropped = int(m_pending / m_step);
            m_pending = std::fmod(m_pending, m_step);
        }
        out.alpha = m_pending / m_step;
        return out;
    }

    void reset() { m_pending = 0; }
    double step() const { return m_step; }

private:
    double m_step;
    int m_maxSteps;
    double m_pending = 0;    // time gathered towards the next step, always below a step
};
