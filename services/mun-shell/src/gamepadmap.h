// A gamepad's events, as Linux's input layer reports them, turned into the
// menus' navigation: standard C++ only (no Qt, no kernel headers), so the
// rules are tested on any host (tests/gamepadmap_test.cpp). gamepads.cpp
// feeds them the events it reads from each pad's /dev/input/event* node.
//
// The kernel's drivers report pads in one vocabulary (the Linux gamepad
// specification): the face button at the bottom is BTN_SOUTH and the one on
// the right BTN_EAST, whatever their labels, the directional pad is a hat
// (ABS_HAT0X/Y) or four buttons (BTN_DPAD_*), and the left stick is ABS_X/Y.
// The menus use that much: the pad and the left stick move the focus, south
// accepts and east goes back; other buttons and axes are not used here.
//
// A direction held repeats as a keyboard's key does: first after
// kRepeatDelayMs, then every kRepeatPeriodMs. The stick is a direction once
// it is pushed past kStickOn of its travel from the centre and until it comes
// back within kStickOff (the gap keeps a stick resting near the threshold
// from flickering); pushed diagonally, the axis pushed further counts. When
// several directions are held, the last one pressed is the one that acts.
#pragma once

#include <array>
#include <vector>

namespace gamepad {

// Event types and codes from linux/input-event-codes.h (a stable kernel ABI).
inline constexpr int kEvSyn = 0x00;
inline constexpr int kEvKey = 0x01;
inline constexpr int kEvAbs = 0x03;
inline constexpr int kSynDropped = 3;
inline constexpr int kAbsX = 0x00;
inline constexpr int kAbsY = 0x01;
inline constexpr int kAbsHat0X = 0x10;
inline constexpr int kAbsHat0Y = 0x11;
inline constexpr int kBtnSouth = 0x130;
inline constexpr int kBtnEast = 0x131;
inline constexpr int kBtnDpadUp = 0x220;
inline constexpr int kBtnDpadDown = 0x221;
inline constexpr int kBtnDpadLeft = 0x222;
inline constexpr int kBtnDpadRight = 0x223;

inline constexpr long long kRepeatDelayMs = 400;
inline constexpr long long kRepeatPeriodMs = 80;
inline constexpr double kStickOn = 0.6;
inline constexpr double kStickOff = 0.4;

enum class Action { Up, Down, Left, Right, Accept, Back };

// A press, a repeat of a direction held (pressed and repeat both true) or a
// release.
struct Event {
    Action action;
    bool pressed;
    bool repeat = false;
    bool operator==(const Event &) const = default;
};

// An absolute axis's range (EVIOCGABS).
struct Range {
    int min = 0;
    int max = 0;
};

class Mapper {
public:
    // The range of one of the pad's axes, from its capabilities; an axis with
    // no range (or an empty one) is not used as a stick.
    void setRange(int code, Range range);

    // One input event (type, code, value) at `ms` on a steady clock: what it
    // presses and releases. A report the kernel dropped (SYN_DROPPED) leaves
    // nothing held.
    std::vector<Event> feed(int type, int code, int value, long long ms);

    // The repeats of the direction held that are due by `ms`.
    std::vector<Event> tick(long long ms);

    // When the next repeat is due, or -1 when no direction is held.
    long long nextDue() const { return m_direction >= 0 ? m_nextRepeat : -1; }

    // The pad is gone: the releases of what it held, and nothing held after.
    std::vector<Event> releaseAll();

private:
    // The four directions, indexed by Action (Up..Right), held by each source.
    struct Sources {
        bool hat = false;
        bool buttons = false;
        bool stick = false;
        bool any() const { return hat || buttons || stick; }
    };
    void setHeld(int direction, bool Sources::*source, bool on, long long ms);
    void settle(long long ms, std::vector<Event> &out);
    void stickMoved(long long ms);
    double normalised(int code) const;

    std::array<Sources, 4> m_held{};
    std::array<long long, 4> m_since{};  // when each direction was last pressed
    std::array<Range, 2> m_ranges{};     // ABS_X, ABS_Y
    std::array<int, 2> m_stick{};        // their last values
    std::array<bool, 2> m_stickSeen{};
    int m_stickDirection = -1;           // the stick's direction, or -1
    int m_direction = -1;                // the direction acting, or -1
    long long m_nextRepeat = 0;
    bool m_accept = false;
    bool m_back = false;
};

} // namespace gamepad
