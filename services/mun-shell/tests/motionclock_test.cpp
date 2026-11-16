// Host-side checks of Shape motion's clock (src/motionclock.h): at the
// display's own rate the shown scene time must advance by the time that
// passed, every frame, with no frame repeating the last one's time and none
// jumping ahead, at 60, 59.94 and 30 Hz and with uneven frames; a stall is
// caught up a few steps at once and the rest dropped. Any C++20 compiler; no
// Qt. Run by `make check` (tests/Makefile here).
#include "motionclock.h"

#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

namespace {

int g_passed = 0;
int g_failed = 0;

void check(const std::string &what, bool ok, const std::string &detail = {})
{
    if (ok) {
        ++g_passed;
    } else {
        ++g_failed;
        std::printf("FAIL %s%s%s\n", what.c_str(), detail.empty() ? "" : ": ", detail.c_str());
    }
}

// The scene time a frame shows, as MotionScene draws it: the step before the
// last plus alpha of a step.
struct Run {
    explicit Run(double step) : clock(step) {}
    MotionClock clock;
    long steps = 0;
    int dropped = 0;
    double shown(double elapsed)
    {
        const MotionClock::Advance a = clock.advance(elapsed);
        steps += a.steps;
        dropped += a.dropped;
        return (double(steps) - 1 + a.alpha) * clock.step();
    }
};

// Frames of `deltas`: after a second's warm-up, every frame's shown time
// must have moved on by its own delta (to a microsecond).
void evenly(const std::string &name, double step, const std::vector<double> &deltas)
{
    Run run(step);
    double last = run.shown(0);
    int holds = 0, off = 0;
    double worst = 0;
    double warm = 0;
    for (double delta : deltas) {
        const double now = run.shown(delta);
        warm += delta;
        if (warm > 1.0) {
            const double moved = now - last;
            if (moved < delta * 0.5)
                ++holds;
            const double error = std::fabs(moved - delta);
            if (error > 1e-6)
                ++off;
            worst = std::max(worst, error);
        }
        last = now;
    }
    char detail[160];
    std::snprintf(detail, sizeof detail, "%d frames held, %d off their delta, worst %.3f ms", holds, off, worst * 1e3);
    check(name + ": the shown time advances by each frame's own time", holds == 0 && off == 0, detail);
}

std::vector<double> uniform(double hz, int frames)
{
    return std::vector<double>(size_t(frames), 1.0 / hz);
}

// Uneven frames round 1/60 s (±4 ms), from a fixed seed.
std::vector<double> jittered(int frames)
{
    std::vector<double> out;
    unsigned state = 1969;
    for (int i = 0; i < frames; ++i) {
        state = state * 1103515245u + 12345u;
        const double u = double((state >> 8) & 0xffff) / 65535.0;
        out.push_back(1.0 / 60 + (u - 0.5) * 0.008);
    }
    return out;
}

} // namespace

int main()
{
    for (const double rate : {20.0, 30.0}) {
        const double step = 1 / rate;
        const std::string sim = std::to_string(int(rate)) + " steps a second";
        evenly(sim + ", 60 Hz", step, uniform(60, 900));
        evenly(sim + ", 59.94 Hz", step, uniform(59.94, 900));
        evenly(sim + ", 30 Hz", step, uniform(30, 450));
        evenly(sim + ", uneven frames", step, jittered(900));
    }

    // A stall of half a second: three steps at once, the rest dropped; the
    // shown time never goes back, and then advances evenly again.
    {
        Run run(0.05);
        double last = run.shown(0);
        bool back = false;
        for (int i = 0; i < 120; ++i) {
            const double now = run.shown(1.0 / 60);
            back |= now < last - 1e-12;
            last = now;
        }
        const long before = run.steps;
        const double stalled = run.shown(0.52);
        check("a stall takes three steps at once", run.steps - before == 3);
        check("a stall drops the whole steps beyond them (7 of 10.4)", run.dropped == 7);
        back |= stalled < last - 1e-12;
        last = stalled;
        int holds = 0;
        for (int i = 0; i < 120; ++i) {
            const double now = run.shown(1.0 / 60);
            back |= now < last - 1e-12;
            holds += now - last < 1.0 / 120;
            last = now;
        }
        check("the shown time never goes back", !back);
        check("after a stall it advances evenly again", holds == 0);
    }

    // Held still (no time passes): nothing is stepped and alpha stays put.
    {
        MotionClock clock(0.05);
        clock.advance(0.02);
        const MotionClock::Advance a = clock.advance(0);
        check("no time, no step", a.steps == 0 && std::fabs(a.alpha - 0.4) < 1e-9);
    }

    std::printf("motion clock: %d passed, %d failed\n", g_passed, g_failed);
    return g_failed == 0 ? 0 : 1;
}
