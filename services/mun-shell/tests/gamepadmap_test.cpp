// Host-side checks of the gamepad rules (src/gamepadmap.*): the events a pad
// sends, as the kernel reports them, and the navigation the menus get. Any
// C++20 compiler; no Qt, no device. Run by `make check` (tests/Makefile here).
#include "gamepadmap.h"

#include <cstdio>
#include <string>
#include <vector>

using namespace gamepad;

namespace {

int g_passed = 0;
int g_failed = 0;

void check(const std::string &what, bool ok)
{
    if (ok) {
        ++g_passed;
    } else {
        ++g_failed;
        std::printf("FAIL %s\n", what.c_str());
    }
}

using Events = std::vector<Event>;

Event press(Action a) { return {a, true}; }
Event repeat(Action a) { return {a, true, true}; }
Event release(Action a) { return {a, false}; }

// A pad whose left stick spans 0..255, as the GameSir Nova Lite reports it.
Mapper pad()
{
    Mapper m;
    m.setRange(kAbsX, {0, 255});
    m.setRange(kAbsY, {0, 255});
    return m;
}

void buttons()
{
    Mapper m = pad();
    check("south accepts", m.feed(kEvKey, kBtnSouth, 1, 0) == Events{press(Action::Accept)});
    check("the kernel's own repeat of a button adds nothing", m.feed(kEvKey, kBtnSouth, 2, 5).empty());
    check("south let go", m.feed(kEvKey, kBtnSouth, 0, 10) == Events{release(Action::Accept)});
    check("east goes back", m.feed(kEvKey, kBtnEast, 1, 20) == Events{press(Action::Back)});
    check("east let go", m.feed(kEvKey, kBtnEast, 0, 30) == Events{release(Action::Back)});
    check("other buttons do nothing", m.feed(kEvKey, 0x133, 1, 40).empty() && m.feed(kEvKey, 0x13c, 1, 50).empty());
    check("a button held does not repeat", m.nextDue() == -1);
}

void pad_directions()
{
    Mapper m = pad();
    check("hat down", m.feed(kEvAbs, kAbsHat0Y, 1, 0) == Events{press(Action::Down)});
    check("hat back to centre", m.feed(kEvAbs, kAbsHat0Y, 0, 50) == Events{release(Action::Down)});
    check("hat left", m.feed(kEvAbs, kAbsHat0X, -1, 60) == Events{press(Action::Left)});
    check("hat left let go", m.feed(kEvAbs, kAbsHat0X, 0, 70) == Events{release(Action::Left)});
    check("pad buttons: up", m.feed(kEvKey, kBtnDpadUp, 1, 80) == Events{press(Action::Up)});
    check("pad buttons: up let go", m.feed(kEvKey, kBtnDpadUp, 0, 90) == Events{release(Action::Up)});
}

void last_pressed_acts()
{
    Mapper m = pad();
    m.feed(kEvAbs, kAbsHat0Y, 1, 0);
    check("a second direction takes over", m.feed(kEvKey, kBtnDpadRight, 1, 10)
                                               == Events{release(Action::Down), press(Action::Right)});
    check("let go, the first one still held acts again", m.feed(kEvKey, kBtnDpadRight, 0, 20)
                                                             == Events{release(Action::Right), press(Action::Down)});
    check("then nothing held", m.feed(kEvAbs, kAbsHat0Y, 0, 30) == Events{release(Action::Down)});
}

void stick()
{
    Mapper m = pad();
    check("centred: nothing", m.feed(kEvAbs, kAbsY, 128, 0).empty() && m.feed(kEvAbs, kAbsX, 128, 0).empty());
    check("short of the threshold: nothing", m.feed(kEvAbs, kAbsY, 200, 10).empty());
    check("pushed down past it", m.feed(kEvAbs, kAbsY, 255, 20) == Events{press(Action::Down)});
    check("coming back, still past the release threshold: held", m.feed(kEvAbs, kAbsY, 190, 30).empty());
    check("within it: let go", m.feed(kEvAbs, kAbsY, 170, 40) == Events{release(Action::Down)});
    check("up is the axis's low end", m.feed(kEvAbs, kAbsY, 0, 50) == Events{press(Action::Up)});
    m.feed(kEvAbs, kAbsY, 128, 60);

    // Diagonally, the axis pushed further counts.
    m.feed(kEvAbs, kAbsY, 230, 70);
    check("diagonal: the further axis", m.feed(kEvAbs, kAbsX, 255, 80)
                                            == Events{release(Action::Down), press(Action::Right)});
    check("the other axis pushed further takes over", m.feed(kEvAbs, kAbsY, 255, 90).empty()
                                                          && m.feed(kEvAbs, kAbsX, 200, 100)
                                                                 == Events{release(Action::Right), press(Action::Down)});

    Mapper unknown;
    check("an axis without a range is not a stick", unknown.feed(kEvAbs, kAbsY, 255, 0).empty());
}

void repeats()
{
    Mapper m = pad();
    m.feed(kEvAbs, kAbsHat0Y, 1, 1000);
    check("first repeat after the delay", m.nextDue() == 1000 + kRepeatDelayMs);
    check("nothing before it", m.tick(1000 + kRepeatDelayMs - 1).empty());
    check("then a repeat", m.tick(1000 + kRepeatDelayMs) == Events{repeat(Action::Down)});
    check("and every period", m.tick(1000 + kRepeatDelayMs + kRepeatPeriodMs) == Events{repeat(Action::Down)});
    check("a late tick gives one repeat, not a burst", m.tick(3000) == Events{repeat(Action::Down)}
                                                           && m.nextDue() == 3000 + kRepeatPeriodMs);
    m.feed(kEvAbs, kAbsHat0Y, 0, 3010);
    check("let go: nothing due", m.nextDue() == -1 && m.tick(5000).empty());
}

void gone()
{
    Mapper m = pad();
    m.feed(kEvAbs, kAbsHat0X, 1, 0);
    m.feed(kEvKey, kBtnSouth, 1, 0);
    check("the pad gone: what it held is let go",
          m.releaseAll() == Events{release(Action::Right), release(Action::Accept)});
    check("and nothing is held after", m.releaseAll().empty() && m.nextDue() == -1);

    m.feed(kEvAbs, kAbsY, 255, 10);
    m.feed(kEvKey, kBtnEast, 1, 10);
    check("a dropped report lets everything go",
          m.feed(kEvSyn, kSynDropped, 0, 20) == Events{release(Action::Down), release(Action::Back)});
}

} // namespace

int main()
{
    buttons();
    pad_directions();
    last_pressed_acts();
    stick();
    repeats();
    gone();
    std::printf("gamepad rules: %d passed, %d failed\n", g_passed, g_failed);
    return g_failed ? 1 : 0;
}
