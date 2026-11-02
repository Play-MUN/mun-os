#include "gamepadmap.h"

#include <algorithm>
#include <cmath>

namespace gamepad {

namespace {

constexpr int kUp = int(Action::Up);
constexpr int kDown = int(Action::Down);
constexpr int kLeft = int(Action::Left);
constexpr int kRight = int(Action::Right);

// The direction a stick axis (0: x, 1: y) points to at `n`, from -1 to 1:
// the kernel's y grows downwards.
int directionOf(int axis, double n)
{
    if (axis == 0)
        return n < 0 ? kLeft : kRight;
    return n < 0 ? kUp : kDown;
}

} // namespace

void Mapper::setRange(int code, Range range)
{
    if (code == kAbsX || code == kAbsY)
        m_ranges[code - kAbsX] = range;
}

double Mapper::normalised(int axis) const
{
    const Range &range = m_ranges[axis];
    if (!m_stickSeen[axis] || range.max <= range.min)
        return 0;
    const double centre = (range.min + range.max) / 2.0;
    const double half = (range.max - range.min) / 2.0;
    return std::clamp((m_stick[axis] - centre) / half, -1.0, 1.0);
}

void Mapper::setHeld(int direction, bool Sources::*source, bool on, long long ms)
{
    Sources &held = m_held[direction];
    if (on && !held.any())
        m_since[direction] = ms;
    held.*source = on;
    // A spent direction acts again once nothing holds it.
    if (!held.any())
        m_spent[direction] = false;
}

void Mapper::key(bool &held, bool &spent, int value, Action action, std::vector<Event> &out)
{
    // 1 pressed, 2 the kernel's own repeat (still pressed), 0 released.
    const bool on = value != 0;
    if (spent) {
        if (!on) {
            held = false;
            spent = false;
        }
        return;
    }
    if (on == held)
        return;
    held = on;
    // A repeat of a press never seen (lost with a dropped report) is not one.
    if (on && value == 2) {
        spent = true;
        return;
    }
    out.push_back({action, on});
}

void Mapper::stickMoved(long long ms)
{
    const double n[2] = {normalised(0), normalised(1)};
    const int current = m_stickDirection;
    int next = -1;
    if (current >= 0) {
        // Kept while its axis stays past kStickOff on its side, unless the
        // other axis is now pushed past kStickOn and further.
        const int axis = current == kLeft || current == kRight ? 0 : 1;
        const double own = n[axis];
        const double other = n[1 - axis];
        if (directionOf(axis, own) == current && std::fabs(own) >= kStickOff
                && !(std::fabs(other) >= kStickOn && std::fabs(other) > std::fabs(own)))
            next = current;
    }
    if (next < 0 && std::max(std::fabs(n[0]), std::fabs(n[1])) >= kStickOn)
        next = std::fabs(n[0]) >= std::fabs(n[1]) ? directionOf(0, n[0]) : directionOf(1, n[1]);
    if (next == current)
        return;
    if (current >= 0)
        setHeld(current, &Sources::stick, false, ms);
    if (next >= 0)
        setHeld(next, &Sources::stick, true, ms);
    m_stickDirection = next;
}

void Mapper::settle(long long ms, std::vector<Event> &out)
{
    // The direction pressed last among those held; the one acting keeps it
    // on a tie (one report that moves the hat diagonally).
    int best = -1;
    for (int d = 0; d < 4; ++d) {
        if (m_held[d].any() && !m_spent[d] && (best < 0 || m_since[d] > m_since[best]))
            best = d;
    }
    if (best >= 0 && m_direction >= 0 && m_held[m_direction].any() && m_since[m_direction] >= m_since[best])
        best = m_direction;
    if (best == m_direction)
        return;
    if (m_direction >= 0)
        out.push_back({Action(m_direction), false});
    m_direction = best;
    if (best >= 0) {
        out.push_back({Action(best), true});
        m_nextRepeat = ms + kRepeatDelayMs;
    }
}

std::vector<Event> Mapper::feed(int type, int code, int value, long long ms)
{
    // A dropped report: its events are invalid through the next SYN_REPORT,
    // and after it the device's state is due before anything counts.
    if (m_sync == Sync::Dropped) {
        if (type == kEvSyn && code == kSynReport)
            m_sync = Sync::Stale;
        return {};
    }
    if (m_sync == Sync::Stale)
        return {};
    if (type == kEvSyn && code == kSynDropped) {
        m_sync = Sync::Dropped;
        return {};
    }
    std::vector<Event> out;
    if (type == kEvKey) {
        const bool on = value != 0;
        const int accept = m_nintendo ? kBtnEast : kBtnSouth;
        const int back = m_nintendo ? kBtnSouth : kBtnEast;
        if (code == accept)
            key(m_accept, m_acceptSpent, value, Action::Accept, out);
        else if (code == back)
            key(m_back, m_backSpent, value, Action::Back, out);
        int direction = -1;
        switch (code) {
        case kBtnDpadUp:
            direction = kUp;
            break;
        case kBtnDpadDown:
            direction = kDown;
            break;
        case kBtnDpadLeft:
            direction = kLeft;
            break;
        case kBtnDpadRight:
            direction = kRight;
            break;
        default:
            break;
        }
        if (direction >= 0) {
            // A repeat of a press never seen does not press the direction.
            const bool unseen = value == 2 && !m_held[direction].any();
            setHeld(direction, &Sources::buttons, on, ms);
            if (unseen)
                m_spent[direction] = true;
        }
    } else if (type == kEvAbs) {
        switch (code) {
        case kAbsHat0X:
            setHeld(kLeft, &Sources::hat, value < 0, ms);
            setHeld(kRight, &Sources::hat, value > 0, ms);
            break;
        case kAbsHat0Y:
            setHeld(kUp, &Sources::hat, value < 0, ms);
            setHeld(kDown, &Sources::hat, value > 0, ms);
            break;
        case kAbsX:
        case kAbsY:
            m_stick[code - kAbsX] = value;
            m_stickSeen[code - kAbsX] = true;
            stickMoved(ms);
            break;
        default:
            break;
        }
    }
    settle(ms, out);
    return out;
}

std::vector<Event> Mapper::tick(long long ms)
{
    std::vector<Event> out;
    if (m_sync != Sync::Synced || m_direction < 0 || ms < m_nextRepeat)
        return out;
    out.push_back({Action(m_direction), true, true});
    // One repeat a tick: a tick that comes late does not make up for it with
    // a burst of moves.
    m_nextRepeat = ms - m_nextRepeat >= kRepeatPeriodMs ? ms + kRepeatPeriodMs : m_nextRepeat + kRepeatPeriodMs;
    return out;
}

std::vector<Event> Mapper::releaseAll()
{
    // A spent key or direction never had a press delivered: no release.
    std::vector<Event> out;
    if (m_direction >= 0)
        out.push_back({Action(m_direction), false});
    if (m_accept && !m_acceptSpent)
        out.push_back({Action::Accept, false});
    if (m_back && !m_backSpent)
        out.push_back({Action::Back, false});
    m_held = {};
    m_spent = {};
    m_stickDirection = -1;
    m_stickSeen = {};
    m_direction = -1;
    m_accept = false;
    m_back = false;
    m_acceptSpent = false;
    m_backSpent = false;
    m_sync = Sync::Synced;
    return out;
}

std::vector<Event> Mapper::resync(const std::optional<State> &state, long long ms)
{
    if (!state)
        return releaseAll();
    m_sync = Sync::Synced;
    std::vector<Event> out;
    const auto held = [&](int code) {
        return std::find(state->keys.cbegin(), state->keys.cend(), code) != state->keys.cend();
    };
    // The buttons: a release if let go, nothing if still held, spent if held
    // now but not seen pressed.
    const auto button = [&](bool &was, bool &spent, bool now, Action action) {
        if (was && !now) {
            if (!spent)
                out.push_back({action, false});
            was = false;
            spent = false;
        } else if (!was && now) {
            was = true;
            spent = true;
        }
    };
    button(m_accept, m_acceptSpent, held(m_nintendo ? kBtnEast : kBtnSouth), Action::Accept);
    button(m_back, m_backSpent, held(m_nintendo ? kBtnSouth : kBtnEast), Action::Back);

    // The directions, from each source as it is now; the stick from its
    // position alone (kStickOn), as if it had just moved there.
    std::array<Sources, 4> now{};
    now[kLeft].hat = state->hatX < 0;
    now[kRight].hat = state->hatX > 0;
    now[kUp].hat = state->hatY < 0;
    now[kDown].hat = state->hatY > 0;
    now[kUp].buttons = held(kBtnDpadUp);
    now[kDown].buttons = held(kBtnDpadDown);
    now[kLeft].buttons = held(kBtnDpadLeft);
    now[kRight].buttons = held(kBtnDpadRight);
    const std::optional<int> stick[2] = {state->stickX, state->stickY};
    for (int axis = 0; axis < 2; ++axis) {
        m_stickSeen[axis] = stick[axis].has_value();
        m_stick[axis] = stick[axis].value_or(0);
    }
    const double n[2] = {normalised(0), normalised(1)};
    m_stickDirection = -1;
    if (std::max(std::fabs(n[0]), std::fabs(n[1])) >= kStickOn)
        m_stickDirection = std::fabs(n[0]) >= std::fabs(n[1]) ? directionOf(0, n[0]) : directionOf(1, n[1]);
    if (m_stickDirection >= 0)
        now[m_stickDirection].stick = true;
    for (int d = 0; d < 4; ++d) {
        const bool was = m_held[d].any();
        m_held[d] = now[d];
        if (!m_held[d].any())
            m_spent[d] = false;
        else if (!was)
            m_spent[d] = true;   // pressed while events were lost: not acted on
    }
    // The direction acting: released if let go; kept if still held, its
    // repeats starting afresh after the delay, not from a clock that ran on
    // while the state was unknown.
    if (m_direction >= 0 && !m_held[m_direction].any()) {
        out.push_back({Action(m_direction), false});
        m_direction = -1;
    } else if (m_direction >= 0) {
        m_nextRepeat = ms + kRepeatDelayMs;
    }
    // Another direction held before the loss and still held may act, as when
    // the acting one is let go; a spent one may not.
    settle(ms, out);
    return out;
}

} // namespace gamepad
