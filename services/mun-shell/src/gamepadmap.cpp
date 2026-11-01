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
        if (m_held[d].any() && (best < 0 || m_since[d] > m_since[best]))
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
    if (type == kEvSyn && code == kSynDropped)
        return releaseAll();
    std::vector<Event> out;
    if (type == kEvKey) {
        // 1 pressed, 2 the kernel's own repeat (still pressed), 0 released.
        const bool on = value != 0;
        const int accept = m_nintendo ? kBtnEast : kBtnSouth;
        const int back = m_nintendo ? kBtnSouth : kBtnEast;
        if (code == accept) {
            if (on != m_accept) {
                m_accept = on;
                out.push_back({Action::Accept, on});
            }
        } else if (code == back) {
            if (on != m_back) {
                m_back = on;
                out.push_back({Action::Back, on});
            }
        }
        switch (code) {
        case kBtnDpadUp:
            setHeld(kUp, &Sources::buttons, on, ms);
            break;
        case kBtnDpadDown:
            setHeld(kDown, &Sources::buttons, on, ms);
            break;
        case kBtnDpadLeft:
            setHeld(kLeft, &Sources::buttons, on, ms);
            break;
        case kBtnDpadRight:
            setHeld(kRight, &Sources::buttons, on, ms);
            break;
        default:
            break;
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
    if (m_direction < 0 || ms < m_nextRepeat)
        return out;
    out.push_back({Action(m_direction), true, true});
    // One repeat a tick: a tick that comes late does not make up for it with
    // a burst of moves.
    m_nextRepeat = ms - m_nextRepeat >= kRepeatPeriodMs ? ms + kRepeatPeriodMs : m_nextRepeat + kRepeatPeriodMs;
    return out;
}

std::vector<Event> Mapper::releaseAll()
{
    std::vector<Event> out;
    if (m_direction >= 0)
        out.push_back({Action(m_direction), false});
    if (m_accept)
        out.push_back({Action::Accept, false});
    if (m_back)
        out.push_back({Action::Back, false});
    m_held = {};
    m_stickDirection = -1;
    m_stickSeen = {};
    m_direction = -1;
    m_accept = false;
    m_back = false;
    return out;
}

} // namespace gamepad
