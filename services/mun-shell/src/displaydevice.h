// Finding the display and choosing its first mode, from what the kernel
// reports: standard C++ only (no Qt, no libdrm), so the rules are tested on
// any host with a fake sysfs tree and plain mode lists
// (tests/displaydevice_test.cpp). displaymode.cpp feeds them the real
// /sys/class/drm and the modes libdrm reads from the connector.
//
// The device. Qt 6.8's framebuffer platform (linuxfb with QT_QPA_FB_DRM)
// opens /dev/dri/card0 whatever its KMS configuration names, so the shell can
// draw only on card0. card0 is a usable display when it is not a framebuffer
// the firmware left behind (simpledrm and the like, which the real driver
// replaces) and one of its connectors is connected. A display connected on
// another card is reported as such: making the display card0 belongs to the
// image (its kernel and command line), not to the shell.
//
// The first mode. A board's image may cap the mode the shell starts in when
// the player's choice is Automatic (MUN_DISPLAY_MAX, "1920x1080@60"). Without
// a cap the display's own mode applies, as it always has.
#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace display {

// The exit status when no usable display appears in time (EX_UNAVAILABLE):
// a failure, so systemd starts the shell again (deploy/mun-shell.service).
constexpr int kNoDisplayStatus = 69;

// The one device Qt's framebuffer platform opens.
inline constexpr std::string_view kCard = "card0";

// One DRM device as /sys/class/drm describes it.
struct Card {
    std::string name;      // "card1"
    int index = -1;        // 1
    std::string driver;    // the kernel driver of its device, "msm_dpu"; empty when unknown
    bool firmware = false; // a framebuffer the firmware left: no modes of its own
    struct Connector {
        std::string name;   // "HDMI-A-1"
        std::string status; // "connected", "disconnected", "unknown"
    };
    std::vector<Connector> connectors; // in name order
};

// The DRM devices under `drm` (/sys/class/drm), by index; render nodes and
// connector entries are not devices. A missing or unreadable tree gives none.
std::vector<Card> scanCards(const std::filesystem::path &drm);

struct Display {
    std::string card;      // "card0"
    std::string connector; // "HDMI-A-1", the kernel's name
    std::string driver;
};

// card0's first connected connector, unless card0 is a firmware framebuffer.
std::optional<Display> usableDisplay(const std::vector<Card> &cards);

// What there is, for the log when no display is usable: a line per card,
// one per framebuffer device under `graphics` (/sys/class/graphics), and a
// last one that says why none is usable.
std::vector<std::string> describe(const std::vector<Card> &cards, const std::filesystem::path &graphics);

// DRM_MODE_FLAG_* (drm_mode.h), the ones the choice looks at;
// displaymode.cpp checks them against libdrm's.
constexpr std::uint32_t kFlagPHSync = 1u << 0;
constexpr std::uint32_t kFlagNHSync = 1u << 1;
constexpr std::uint32_t kFlagPVSync = 1u << 2;
constexpr std::uint32_t kFlagNVSync = 1u << 3;
constexpr std::uint32_t kFlagInterlace = 1u << 4;
constexpr std::uint32_t kFlagDoubleScan = 1u << 5;

// A mode as the connector lists it (drmModeModeInfo), in its order.
struct Timing {
    std::uint32_t clock = 0; // kHz
    std::uint16_t hdisplay = 0, hsyncStart = 0, hsyncEnd = 0, htotal = 0;
    std::uint16_t vdisplay = 0, vsyncStart = 0, vsyncEnd = 0, vtotal = 0;
    std::uint32_t flags = 0;
    bool preferred = false;
};

// The refresh rate in millihertz, rounded, as the kernel computes it
// (drm_mode_vrefresh) but without dropping the fraction: 60.000 and 59.940
// stay apart. 0 for a mode without totals.
std::uint32_t refreshMilliHz(const Timing &mode);

// "1920x1080@60.000".
std::string modeName(const Timing &mode);

struct Cap {
    int width = 0;
    int height = 0;
    int hz = 0;
};

// "1920x1080@60"; nothing for anything else.
std::optional<Cap> parseCap(std::string_view text);

enum class Pick {
    Target,   // the cap's size at its rate (60 or 60/1.001 Hz for 60)
    Fallback, // within the cap, but not the target: logged as such
    AboveCap, // nothing fits the cap: the display's preferred mode, for a picture
};

struct Choice {
    std::size_t index = 0; // into the list given
    Pick pick = Pick::Target;
};

// The mode to start in under `cap`, in this order:
//   1. the cap's size at its rate, the exact rate first (60.000 before
//      59.940), then the preferred mode, then the list order;
//   2. the largest mode within the cap at that rate (1280x720@60);
//   3. the cap's size at a lower rate (1920x1080@50);
//   4. the largest mode within the cap at any rate up to it;
//   5. the preferred mode (else the first), above the cap.
// Interlaced and double-scan modes are never chosen; nothing when no other
// mode is listed.
std::optional<Choice> chooseMode(const std::vector<Timing> &modes, const Cap &cap);

// The mode as a modeline for Qt's KMS configuration: the connector's own
// timings, so Qt sets exactly this mode rather than the first one with the
// same size and whole-hertz rate. Nothing when a modeline cannot say it
// (no sync polarity, interlaced, double scan).
std::optional<std::string> modeline(const Timing &mode);

} // namespace display
