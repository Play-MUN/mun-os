// Host-side checks of the gamepad rules (src/gamepadmap.*): the events a pad
// sends, as the kernel reports them, and the navigation the menus get. Any
// C++20 compiler; no Qt, no device. Run by `make check` (tests/Makefile here).
#include "gamepadmap.h"

#include <cstdio>
#include <optional>
#include <string>
#include <utility>
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
// A pad's state with these keys held, at rest otherwise.
State holding(std::vector<int> keys)
{
    State state;
    state.keys = std::move(keys);
    return state;
}

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

void nintendo()
{
    Mapper m = pad();
    m.setNintendo(true);
    check("Nintendo's layout: A (east) accepts", m.feed(kEvKey, kBtnEast, 1, 0) == Events{press(Action::Accept)});
    check("Nintendo's layout: A let go", m.feed(kEvKey, kBtnEast, 0, 10) == Events{release(Action::Accept)});
    check("Nintendo's layout: B (south) goes back", m.feed(kEvKey, kBtnSouth, 1, 20) == Events{press(Action::Back)});
    check("Nintendo's layout: B let go", m.feed(kEvKey, kBtnSouth, 0, 30) == Events{release(Action::Back)});
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
}

// The kernel dropped events (SYN_DROPPED): everything through the next
// SYN_REPORT is ignored, then the pad's state is due, and nothing is pressed
// by resuming from it.
void dropped()
{
    {
        // The case as it came: Accept held, events lost, a stale Accept and
        // Down before the report, then the repeat timer.
        Mapper m = pad();
        m.feed(kEvKey, kBtnSouth, 1, 0);
        check("a drop: nothing at once", m.feed(kEvSyn, kSynDropped, 0, 100).empty());
        check("a stale Accept: nothing", m.feed(kEvKey, kBtnSouth, 1, 101).empty());
        check("a stale Down: nothing", m.feed(kEvAbs, kAbsHat0Y, 1, 102).empty());
        check("the report ends the drop: the state is due",
              m.feed(kEvSyn, kSynReport, 0, 103).empty() && m.waitingForState());
        check("no repeat while it is due", m.nextDue() == -1 && m.tick(503).empty());
        check("events before the state: nothing", m.feed(kEvKey, kBtnSouth, 0, 104).empty());
        check("the state, nothing held: Accept let go, no Down",
              m.resync(State{}, 105) == Events{release(Action::Accept)} && !m.waitingForState());
        check("then no repeat", m.nextDue() == -1 && m.tick(1000).empty());
        check("and a press acts again", m.feed(kEvKey, kBtnSouth, 1, 1100) == Events{press(Action::Accept)});
    }
    {
        // The dropped report spread over several reads.
        Mapper m = pad();
        m.feed(kEvSyn, kSynDropped, 0, 0);
        bool quiet = m.feed(kEvKey, kBtnSouth, 1, 1).empty() && m.feed(kEvSyn, kSynReport + 2, 0, 2).empty();
        quiet = quiet && m.feed(kEvAbs, kAbsHat0X, 1, 3).empty() && m.feed(kEvKey, kBtnEast, 1, 4).empty();
        check("over several reads: nothing until the report", quiet && !m.waitingForState());
        m.feed(kEvSyn, kSynReport, 0, 5);
        check("the report, in a later read: the state is due", m.waitingForState());
    }
    {
        // Held through the loss: no new press, its release when let go.
        Mapper m = pad();
        m.feed(kEvKey, kBtnSouth, 1, 0);
        m.feed(kEvSyn, kSynDropped, 0, 10);
        m.feed(kEvSyn, kSynReport, 0, 11);
        check("Accept still held: nothing", m.resync(holding({kBtnSouth}), 12).empty());
        check("its release later", m.feed(kEvKey, kBtnSouth, 0, 20) == Events{release(Action::Accept)});
    }
    {
        // Pressed while events were lost: spent until let go.
        Mapper m = pad();
        m.feed(kEvSyn, kSynDropped, 0, 0);
        m.feed(kEvSyn, kSynReport, 0, 1);
        check("Back held now, never seen pressed: nothing", m.resync(holding({kBtnEast}), 2).empty());
        check("its kernel repeat: nothing", m.feed(kEvKey, kBtnEast, 2, 3).empty());
        check("let go: nothing (no press was given)", m.feed(kEvKey, kBtnEast, 0, 4).empty());
        check("the next press acts", m.feed(kEvKey, kBtnEast, 1, 5) == Events{press(Action::Back)});
    }
    {
        // A direction held through the loss keeps acting, its repeats afresh;
        // one pressed during it does not act.
        Mapper m = pad();
        m.feed(kEvAbs, kAbsHat0Y, 1, 0);
        m.feed(kEvSyn, kSynDropped, 0, 1000);
        m.feed(kEvSyn, kSynReport, 0, 1001);
        State state;
        state.hatY = 1;
        state.keys = {kBtnDpadLeft};
        check("Down still held: nothing new", m.resync(state, 2000).empty());
        check("its repeats start afresh after the delay",
              m.nextDue() == 2000 + kRepeatDelayMs && m.tick(2000 + kRepeatDelayMs - 1).empty());
        check("Left, pressed while lost, does not take over", m.feed(kEvAbs, kAbsHat0Y, 0, 2100) == Events{release(Action::Down)});
        check("nor act while held", m.nextDue() == -1);
        check("let go: nothing", m.feed(kEvKey, kBtnDpadLeft, 0, 2200).empty());
        check("pressed again: it acts", m.feed(kEvKey, kBtnDpadLeft, 1, 2300) == Events{press(Action::Left)});
    }
    {
        // The stick from its position; Nintendo's layout kept.
        Mapper m = pad();
        m.setNintendo(true);
        m.feed(kEvKey, kBtnEast, 1, 0);
        m.feed(kEvSyn, kSynDropped, 0, 1);
        m.feed(kEvSyn, kSynReport, 0, 2);
        State state;
        state.stickX = 128;
        state.stickY = 255;
        check("Nintendo: A (east) let go; the stick pushed down while lost does not move",
              m.resync(state, 3) == Events{release(Action::Accept)} && m.nextDue() == -1);
        check("the stick back to centre: nothing", m.feed(kEvAbs, kAbsY, 128, 4).empty());
        check("pushed again: it moves", m.feed(kEvAbs, kAbsY, 255, 5) == Events{press(Action::Down)});
    }
    {
        // A pad's node that cannot report its state: everything let go.
        Mapper m = pad();
        m.feed(kEvKey, kBtnSouth, 1, 0);
        m.feed(kEvAbs, kAbsHat0X, 1, 0);
        m.feed(kEvSyn, kSynDropped, 0, 1);
        m.feed(kEvSyn, kSynReport, 0, 2);
        check("no state: what was held is let go",
              m.resync(std::nullopt, 3) == Events{release(Action::Right), release(Action::Accept)});
        check("a kernel repeat of a key still held is not a press", m.feed(kEvKey, kBtnSouth, 2, 4).empty());
    }
    {
        // Unplugged while recovering: its releases, and a clean state.
        Mapper m = pad();
        m.feed(kEvKey, kBtnSouth, 1, 0);
        m.feed(kEvSyn, kSynDropped, 0, 1);
        check("gone during the drop: Accept let go", m.releaseAll() == Events{release(Action::Accept)});
        check("and no state is due after", !m.waitingForState() && m.nextDue() == -1);
        check("a press acts", m.feed(kEvKey, kBtnSouth, 1, 2) == Events{press(Action::Accept)});
    }
}

} // namespace

int main()
{
    buttons();
    nintendo();
    pad_directions();
    last_pressed_acts();
    stick();
    repeats();
    gone();
    dropped();
    std::printf("gamepad rules: %d passed, %d failed\n", g_passed, g_failed);
    return g_failed ? 1 : 0;
}
