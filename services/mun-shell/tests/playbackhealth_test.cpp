// Host-side checks of the sound device's health (src/playbackhealth.h): open
// is not playing well. A device whose played frames advance at its rate is
// healthy after a window; one that has not started, stalls, plays slow or
// fast, goes back or cannot say what it holds is not, and is again only after
// a good window; one whose writes stop coming (blocked on a stopped device)
// stops being healthy by itself. Writes come every 10 ms, as the worker's.
// Any C++20 compiler; no Qt. Run by `make check` (tests/Makefile here).
#include "playbackhealth.h"

#include <cmath>
#include <cstdio>
#include <functional>
#include <string>

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

std::string num(double value)
{
    char text[32];
    std::snprintf(text, sizeof text, "%.3f", value);
    return text;
}

constexpr int kRate = 48000;
constexpr double kStep = 0.01;
constexpr long long kHeld = 1920;  // 40 ms in the device, as the shell asks

// Feeds a device opened at 0 for `seconds`: `played(s)` frames played by s,
// or below zero for a write whose delay query failed. Returns the health and
// the problems seen.
struct Fed {
    PlaybackHealth health{kRate};
    int stalled = 0, slow = 0, fast = 0, backwards = 0, unknown = 0;
    double healthyAt(double s) const { return health.healthyFor(s); }
};

Fed feed(double seconds, const std::function<long long(double)> &played)
{
    Fed out;
    out.health.opened(0);
    for (double s = kStep; s <= seconds + 1e-9; s += kStep) {
        const long long p = played(s);
        const long long held = p < 0 ? -1 : kHeld;
        const long long written = (p < 0 ? 0 : p) + kHeld;
        switch (out.health.wrote(s, written, held)) {
        case PlaybackHealth::Problem::Stalled: ++out.stalled; break;
        case PlaybackHealth::Problem::Slow: ++out.slow; break;
        case PlaybackHealth::Problem::Fast: ++out.fast; break;
        case PlaybackHealth::Problem::Backwards: ++out.backwards; break;
        case PlaybackHealth::Problem::Unknown: ++out.unknown; break;
        case PlaybackHealth::Problem::None: break;
        }
    }
    return out;
}

long long atRate(double s, double speed) { return (long long)(s * kRate * speed); }

} // namespace

int main()
{
    {
        const Fed f = feed(3, [](double s) { return atRate(s, 1.0); });
        const double h = f.healthyAt(3.0);
        check("at its rate: healthy, from its first good window", h > 2.4 && h <= 3.0, num(h));
        check("at its rate: no problem", f.stalled + f.slow + f.fast + f.backwards + f.unknown == 0);
        check("at its rate: it played from the start", std::abs(f.health.advancingSince() - kStep) < 1e-9,
              num(f.health.advancingSince()));
    }
    {
        // Open, writes taken into its buffer, nothing played.
        const Fed f = feed(3, [](double) { return 0LL; });
        check("not started: not healthy", f.healthyAt(3.0) < 0);
        check("not started: never played", f.health.advancingSince() < 0);
    }
    for (double speed : {0.3, 0.7}) {
        const Fed f = feed(3, [speed](double s) { return atRate(s, speed); });
        check("at " + num(speed) + "x: never healthy", f.healthyAt(3.0) < 0);
        check("at " + num(speed) + "x: said slow", f.slow > 0);
    }
    {
        const Fed f = feed(3, [](double s) { return atRate(s, 1.6); });
        check("at 1.6x: never healthy, said fast", f.healthyAt(3.0) < 0 && f.fast > 0);
    }
    {
        // Plays, stops 1 s (samples still coming: the device takes no more),
        // plays again: unhealthy from the stall, healthy a window after.
        const Fed f = feed(4, [](double s) { return s < 1.5 ? atRate(s, 1) : s < 2.5 ? atRate(1.5, 1) : atRate(s - 1, 1); });
        check("a stall: said once", f.stalled == 1, std::to_string(f.stalled));
        PlaybackHealth h(kRate);
        h.opened(0);
        for (double s = kStep; s <= 2.0 + 1e-9; s += kStep)
            h.wrote(s, (s < 1.5 ? atRate(s, 1) : atRate(1.5, 1)) + kHeld, kHeld);
        check("a stall: not healthy while stalled", h.healthyFor(2.0) < 0);
        check("a stall: healthy again after it plays a window", f.healthyAt(4.0) > 0.5 && f.healthyAt(4.0) < 1.6,
              num(f.healthyAt(4.0)));
    }
    {
        // Writes that stop coming: a device that stopped blocks the writer.
        PlaybackHealth h(kRate);
        h.opened(0);
        for (double s = kStep; s <= 2.0 + 1e-9; s += kStep)
            h.wrote(s, atRate(s, 1) + kHeld, kHeld);
        check("writes stop: healthy before", h.healthyFor(2.0) > 1.0, num(h.healthyFor(2.0)));
        check("writes stop: not healthy once nothing has played for the gap", h.healthyFor(2.3) < 0,
              num(h.healthyFor(2.3)));
    }
    {
        const Fed f = feed(3, [](double s) { return s < 1.5 ? atRate(s, 1) : atRate(s - 1, 1); });
        check("going back: said", f.backwards == 1, std::to_string(f.backwards));
        check("going back: healthy again a window after", f.healthyAt(3.0) > 0.5, num(f.healthyAt(3.0)));
    }
    {
        // A delay query that fails now and then: unknown, so not healthy.
        const Fed f = feed(3, [](double s) { return std::fmod(s, 0.4) < 0.011 ? -1 : atRate(s, 1); });
        check("cannot say what it holds: said", f.unknown > 0);
        check("cannot say what it holds: not healthy for a whole window", f.healthyAt(3.0) < 0.45,
              num(f.healthyAt(3.0)));
    }
    {
        PlaybackHealth h(kRate);
        check("never opened: not healthy", h.healthyFor(1.0) < 0);
        h.opened(0);
        for (double s = kStep; s <= 2.0 + 1e-9; s += kStep)
            h.wrote(s, atRate(s, 1) + kHeld, kHeld);
        h.closed();
        check("closed: not healthy", h.healthyFor(2.0) < 0 && !h.open());
    }

    std::printf("playback health: %d passed, %d failed\n", g_passed, g_failed);
    return g_failed == 0 ? 0 : 1;
}
