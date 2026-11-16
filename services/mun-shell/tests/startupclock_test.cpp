// Host-side checks of the start-up's clock (src/startupclock.h): the picture
// follows a sound it can trust and goes on alone, at real speed and never
// back, from where it was, when the sound does not start, does not advance
// from its start, stalls, goes back, runs too slow or too fast, or its device
// keeps recovering; a short stall only holds it. Frames come every 1/60 s.
// Any C++20 compiler; no Qt. Run by `make check` (tests/Makefile here).
#include "startupclock.h"

#include <cmath>
#include <cstdio>
#include <functional>
#include <limits>
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

std::string num(double value)
{
    char text[32];
    std::snprintf(text, sizeof text, "%.3f", value);
    return text;
}

const double kNaN = std::numeric_limits<double>::quiet_NaN();
const double kFrame = 1.0 / 60;

// A start-up run frame by frame for `seconds`, the sound given by `heard(s)`
// (s: seconds since begin) and the device's recoveries by `recoveries(s)`.
// What it saw: t on every frame, whether t ever went back, when it stopped
// following and from which t, and the journal's lines.
struct Run {
    std::vector<double> times, ts;
    bool wentBack = false;
    double aloneAt = -1, aloneFrom = -1;
    std::string notes;
    bool followingAtEnd = true;
};

Run run(double seconds, const std::function<double(double)> &heard,
        const std::function<int(double)> &recoveries = [](double) { return 0; })
{
    StartupClock clock;
    Run out;
    const double begun = 100.0;  // any monotonic origin
    clock.begin(begun, true, 0);
    double last = -1e9;
    for (double s = 0; s <= seconds; s += kFrame) {
        const bool was = clock.following();
        const double t = clock.tick(begun + s, heard(s), recoveries(s));
        if (t < last - 1e-9)
            out.wentBack = true;
        last = t;
        if (was && !clock.following() && out.aloneAt < 0) {
            out.aloneAt = s;
            out.aloneFrom = t;
        }
        out.times.push_back(s);
        out.ts.push_back(t);
        const std::string note = clock.takeNote();
        if (!note.empty())
            out.notes += note + "\n";
    }
    out.followingAtEnd = clock.following();
    return out;
}

// t at the frame nearest `s`.
double tAt(const Run &r, double s)
{
    size_t best = 0;
    for (size_t i = 0; i < r.times.size(); ++i)
        if (std::abs(r.times[i] - s) < std::abs(r.times[best] - s))
            best = i;
    return r.ts[best];
}

// Once alone, t advances by the time that passed, from where it was.
void checkAloneAtRealSpeed(const std::string &name, const Run &r)
{
    check(name + ": the picture never goes back", !r.wentBack);
    if (r.aloneAt < 0) {
        check(name + ": it goes on alone", false, "it kept following");
        return;
    }
    const double later = r.aloneAt + 2.0;
    const double advanced = tAt(r, later) - tAt(r, r.aloneAt);
    check(name + ": alone, it runs at real speed", std::abs(advanced - 2.0) < 0.05, num(advanced) + " s in 2 s");
}

} // namespace

int main()
{
    // A sound that plays well: followed to the end, t what is heard.
    {
        const Run r = run(8, [](double s) { return s - 0.05; });
        check("a good sound: followed throughout", r.followingAtEnd && r.aloneAt < 0, r.notes);
        check("a good sound: t is what is heard", std::abs(tAt(r, 5.0) - 4.95) < 1e-6, num(tAt(r, 5.0)));
        check("a good sound: the journal says it follows the sound",
              r.notes.find("the picture follows the sound") != std::string::npos, r.notes);
        check("a good sound: never goes back", !r.wentBack);
    }
    // No first samples: alone from 0 after 3 s; it may follow a late sound
    // while still black.
    {
        StartupClock clock;
        clock.begin(0, true, 0);
        double t = 0;
        for (double s = 0; s <= 3.1; s += kFrame)
            t = clock.tick(s, kNaN, 0);
        check("no sound: alone from 0 after 3 s", !clock.following() && t >= 0 && t < 0.15, num(t));
        clock.started(3.15);
        check("no sound: a sound that starts while still black is followed", clock.following());
        const std::string note = clock.takeNote();
        check("no sound: the journal says both", note.find("no sound after 3000 ms") != std::string::npos
                                                     && note.find("follows it again") != std::string::npos, note);
    }
    // Stuck at its start: samples taken, nothing played.
    for (double stuck : {-0.04, 0.0}) {
        const Run r = run(8, [stuck](double) { return stuck; });
        check("stuck at " + num(stuck) + ": alone after 3 s of no advance",
              r.aloneAt > 2.9 && r.aloneAt < 3.2 && r.aloneFrom == 0, num(r.aloneAt) + " from " + num(r.aloneFrom));
        checkAloneAtRealSpeed("stuck at " + num(stuck), r);
    }
    {
        // Stuck, then a late start must not take the picture back to it.
        StartupClock clock;
        clock.begin(0, true, 0);
        for (double s = 0; s <= 3.2; s += kFrame)
            clock.tick(s, 0.0, 0);
        clock.started(3.3);
        check("stuck: not followed again afterwards", !clock.following());
    }
    // Too slow or too fast: alone within about a second, at real speed.
    for (double speed : {0.3, 0.6, 1.6, 3.0}) {
        const Run r = run(8, [speed](double s) { return speed * s; });
        check("a sound at " + num(speed) + "x: alone within 1.5 s", r.aloneAt > 0 && r.aloneAt < 1.5, num(r.aloneAt));
        check("a sound at " + num(speed) + "x: the journal says how fast",
              r.notes.find("times real time") != std::string::npos, r.notes);
        checkAloneAtRealSpeed("a sound at " + num(speed) + "x", r);
    }
    // A little off real time is still followed (a device's clock drifts).
    for (double speed : {0.9, 1.1}) {
        const Run r = run(8, [speed](double s) { return speed * s; });
        check("a sound at " + num(speed) + "x: still followed", r.followingAtEnd, r.notes);
    }
    // Going back.
    {
        const Run r = run(8, [](double s) { return s < 2 ? s : s - 1.0; });
        check("a sound that goes back: alone at once", r.aloneAt >= 2 && r.aloneAt < 2.05, num(r.aloneAt));
        check("a sound that goes back: from where the picture was", std::abs(r.aloneFrom - 2.0) < 0.05,
              num(r.aloneFrom));
        checkAloneAtRealSpeed("a sound that goes back", r);
    }
    // A short stall holds the picture, which then follows again (not taken
    // for slowness); a long one leaves it alone from there.
    {
        const Run r = run(8, [](double s) { return s < 2 ? s : (s < 3.5 ? 2.0 : s - 1.5); });
        check("a 1.5 s stall: followed again", r.followingAtEnd, r.notes);
        check("a 1.5 s stall: held meanwhile", std::abs(tAt(r, 3.0) - 2.0) < 1e-6, num(tAt(r, 3.0)));
        check("a 1.5 s stall: the journal says the picture held",
              r.notes.find("the picture held") != std::string::npos, r.notes);
    }
    {
        const Run r = run(8, [](double s) { return s < 2 ? s : 2.0; });
        check("a long stall: alone after 2 s", r.aloneAt > 3.9 && r.aloneAt < 4.2, num(r.aloneAt));
        checkAloneAtRealSpeed("a long stall", r);
    }
    // Recoveries: one is followed through, the second is not trusted.
    {
        const Run one = run(8, [](double s) { return s; }, [](double s) { return s < 2 ? 0 : 1; });
        check("one recovery: still followed", one.followingAtEnd, one.notes);
        const Run two = run(8, [](double s) { return s; }, [](double s) { return s < 2 ? 0 : (s < 3 ? 1 : 2); });
        check("two recoveries: alone", two.aloneAt >= 3 && two.aloneAt < 3.05, num(two.aloneAt));
        checkAloneAtRealSpeed("two recoveries", two);
        // Counted from begin(): the device's earlier recoveries do not count.
        StartupClock clock;
        clock.begin(0, true, 5);
        clock.tick(0.1, 0.05, 5);
        check("recoveries before the start-up: not counted", clock.following());
    }
    // Over (played out or dropped while the picture runs): alone from there.
    {
        const Run r = run(8, [](double s) { return s < 2 ? s : kNaN; });
        check("a sound that is over: alone from where it was", r.aloneAt >= 2 && std::abs(r.aloneFrom - 2.0) < 0.05,
              num(r.aloneAt) + " from " + num(r.aloneFrom));
        checkAloneAtRealSpeed("a sound that is over", r);
    }
    {
        StartupClock clock;
        clock.begin(0, true, 0);
        clock.tick(0.5, 0.4, 0);
        clock.dropped(0.6);
        check("dropped: alone", !clock.following());
        clock.started(0.7);
        check("dropped: not followed again", !clock.following());
    }
    // Sounds off: alone from 0 at once.
    {
        StartupClock clock;
        clock.begin(0, false, 0);
        check("sounds off: alone from the start", !clock.following() && std::abs(clock.tick(1.0, kNaN, 0) - 1.0) < 1e-9);
    }

    std::printf("startup clock: %d passed, %d failed\n", g_passed, g_failed);
    return g_failed == 0 ? 0 : 1;
}
