// Host-side checks of the display rules (src/displaydevice.*): which DRM
// device is usable, from fake sysfs trees, and which mode the shell starts in
// under a cap, from mode lists shaped like real displays'. Any C++20
// compiler; no Qt, no display. Run by `make check` (tests/Makefile here).
#include "displaydevice.h"

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <unistd.h>
#include <vector>

namespace fs = std::filesystem;
using namespace display;

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

bool contains(const std::string &text, const std::string &part)
{
    return text.find(part) != std::string::npos;
}

// A fake /sys/class/drm and /sys/class/graphics in a directory of its own.
struct Sysfs {
    fs::path root;
    explicit Sysfs(const std::string &name)
        : root(fs::temp_directory_path() / ("mun-displaydevice-" + std::to_string(getpid()) + "-" + name))
    {
        fs::remove_all(root);
        fs::create_directories(root / "drm");
        fs::create_directories(root / "graphics");
    }
    ~Sysfs() { fs::remove_all(root); }
    fs::path drm() const { return root / "drm"; }
    fs::path graphics() const { return root / "graphics"; }
    void card(const std::string &name, const std::string &driver) const
    {
        fs::create_directories(drm() / name / "device");
        if (!driver.empty())
            fs::create_directory_symlink("/sys/bus/platform/drivers/" + driver, drm() / name / "device" / "driver");
    }
    void connector(const std::string &card, const std::string &name, const std::string &status) const
    {
        fs::create_directories(drm() / (card + "-" + name));
        std::ofstream(drm() / (card + "-" + name) / "status") << status << "\n";
    }
    void framebuffer(const std::string &name, const std::string &id) const
    {
        fs::create_directories(graphics() / name);
        std::ofstream(graphics() / name / "name") << id << "\n";
    }
};

std::string lastLine(const Sysfs &sysfs)
{
    const std::vector<std::string> lines = describe(scanCards(sysfs.drm()), sysfs.graphics());
    return lines.empty() ? std::string() : lines.back();
}

void deviceTests()
{
    {
        // A board's boot: the firmware's framebuffer took card0, the display
        // driver came up as card1.
        Sysfs s("firmware-card0");
        s.card("card0", "simple-framebuffer");
        s.connector("card0", "Unknown-1", "connected");
        s.card("card1", "msm_dpu");
        s.connector("card1", "HDMI-A-1", "connected");
        s.connector("card1", "DP-1", "disconnected");
        s.framebuffer("fb0", "simpledrmdrmfb");
        s.framebuffer("fbcon", "");
        const std::vector<Card> cards = scanCards(s.drm());
        check("firmware card0: two cards", cards.size() == 2);
        check("firmware card0: marked", cards.size() == 2 && cards[0].firmware && !cards[1].firmware);
        check("firmware card0: connectors in name order",
              cards.size() == 2 && cards[1].connectors.size() == 2 && cards[1].connectors[0].name == "DP-1");
        check("firmware card0: not usable", !usableDisplay(cards));
        const std::vector<std::string> lines = describe(cards, s.graphics());
        check("firmware card0: described per card", lines.size() == 4 &&
              lines[0] == "card0: simple-framebuffer (firmware framebuffer); Unknown-1 connected" &&
              lines[1] == "card1: msm_dpu; DP-1 disconnected, HDMI-A-1 connected" && lines[2] == "fb0: simpledrmdrmfb");
        check("firmware card0: says why", contains(lines.back(), "card0 is the firmware's framebuffer") &&
              contains(lines.back(), "connected on card1 (msm_dpu)"));
    }
    {
        Sysfs s("display-card0");
        s.card("card0", "msm_dpu");
        s.connector("card0", "DP-1", "disconnected");
        s.connector("card0", "HDMI-A-1", "connected");
        const std::optional<Display> found = usableDisplay(scanCards(s.drm()));
        check("display on card0: found", found && found->card == "card0" && found->connector == "HDMI-A-1" &&
              found->driver == "msm_dpu");
    }
    {
        Sysfs s("virtual");
        s.card("card0", "virtio_gpu");
        s.connector("card0", "Virtual-1", "connected");
        fs::create_directories(s.drm() / "renderD128");
        s.card("card10", "other");
        const std::vector<Card> cards = scanCards(s.drm());
        check("render node is not a card", cards.size() == 2);
        const std::optional<Display> found = usableDisplay(cards);
        check("virtual display found", found && found->connector == "Virtual-1" && found->driver == "virtio_gpu");
    }
    {
        Sysfs s("order");
        s.card("card10", "a");
        s.card("card2", "b");
        s.connector("card2", "HDMI-A-1", "connected");
        const std::vector<Card> cards = scanCards(s.drm());
        check("cards by number, not by name", cards.size() == 2 && cards[0].name == "card2" && cards[1].name == "card10");
        check("card10's connectors are not card1's", cards.size() == 2 && cards[1].connectors.empty());
    }
    {
        Sysfs s("only-firmware");
        s.card("card0", "simple-framebuffer");
        s.connector("card0", "Unknown-1", "connected");
        check("only the firmware's: not usable", !usableDisplay(scanCards(s.drm())));
        check("only the firmware's: says so", contains(lastLine(s), "firmware's framebuffer") &&
              !contains(lastLine(s), "a display is connected"));
    }
    for (const char *driver : {"efi-framebuffer", "vesa-framebuffer", "ofdrm"}) {
        Sysfs s(std::string("firmware-") + driver);
        s.card("card0", driver);
        s.connector("card0", "Unknown-1", "connected");
        check(std::string("firmware driver ") + driver + ": not usable", !usableDisplay(scanCards(s.drm())));
    }
    {
        // A render-only GPU as card0, the display on card5.
        Sysfs s("render-only");
        s.card("card0", "panthor");
        s.card("card5", "linlon-dp");
        s.connector("card5", "HDMI-A-1", "connected");
        check("render-only card0: not usable", !usableDisplay(scanCards(s.drm())));
        check("render-only card0: points at card5", contains(lastLine(s), "card0 has no connected connector") &&
              contains(lastLine(s), "connected on card5 (linlon-dp)"));
    }
    {
        Sysfs s("nothing-connected");
        s.card("card0", "virtio_gpu");
        s.connector("card0", "Virtual-1", "disconnected");
        check("nothing connected: not usable", !usableDisplay(scanCards(s.drm())));
        check("nothing connected: says so", lastLine(s) == "card0 has no connected connector");
    }
    {
        Sysfs s("no-card0");
        s.card("card1", "msm_dpu");
        s.connector("card1", "HDMI-A-1", "connected");
        check("no card0: not usable", !usableDisplay(scanCards(s.drm())));
        check("no card0: says so", contains(lastLine(s), "there is no card0") && contains(lastLine(s), "card1"));
    }
    {
        const fs::path missing = fs::temp_directory_path() / "mun-displaydevice-missing" / "drm";
        check("no sysfs: no cards", scanCards(missing).empty());
        const std::vector<std::string> lines = describe({}, missing);
        check("no sysfs: described", lines.size() == 3 && lines[0] == "no DRM device" &&
              lines[1] == "no framebuffer device" && lines[2] == "there is no card0");
    }
    {
        Sysfs s("driver-unknown");
        s.card("card0", "");
        s.connector("card0", "HDMI-A-1", "connected");
        const std::vector<Card> cards = scanCards(s.drm());
        check("unknown driver: still usable", usableDisplay(cards).has_value());
        check("unknown driver: said", describe(cards, s.graphics())[0] == "card0: driver unknown; HDMI-A-1 connected");
    }
}

// Modes as the kernel lists them from an EDID. CEA-861 timings; the 59.94 Hz
// variants carry the kernel's clock (the 60 Hz clock * 1000 / 1001, rounded).
Timing mode(std::uint32_t clock, std::uint16_t w, std::uint16_t hss, std::uint16_t hse, std::uint16_t ht,
            std::uint16_t h, std::uint16_t vss, std::uint16_t vse, std::uint16_t vt, bool preferred = false,
            std::uint32_t flags = kFlagPHSync | kFlagPVSync)
{
    Timing t;
    t.clock = clock;
    t.hdisplay = w, t.hsyncStart = hss, t.hsyncEnd = hse, t.htotal = ht;
    t.vdisplay = h, t.vsyncStart = vss, t.vsyncEnd = vse, t.vtotal = vt;
    t.flags = flags;
    t.preferred = preferred;
    return t;
}

const Timing k2160p60 = mode(594000, 3840, 4016, 4104, 4400, 2160, 2168, 2178, 2250);
const Timing k1440p60 = mode(241500, 2560, 2608, 2640, 2720, 1440, 1443, 1448, 1481, false, kFlagPHSync | kFlagNVSync);
const Timing k1080p60 = mode(148500, 1920, 2008, 2052, 2200, 1080, 1084, 1089, 1125);
const Timing k1080p5994 = mode(148352, 1920, 2008, 2052, 2200, 1080, 1084, 1089, 1125);
const Timing k1080p50 = mode(148500, 1920, 2448, 2492, 2640, 1080, 1084, 1089, 1125);
const Timing k1080p120 = mode(297000, 1920, 2008, 2052, 2200, 1080, 1084, 1089, 1125);
const Timing k1080i60 = mode(74250, 1920, 2008, 2052, 2200, 1080, 1084, 1094, 1125, false,
                             kFlagPHSync | kFlagPVSync | kFlagInterlace);
const Timing k720p60 = mode(74250, 1280, 1390, 1430, 1650, 720, 725, 730, 750);
const Timing k720p50 = mode(74250, 1280, 1720, 1760, 1980, 720, 725, 730, 750);

Timing preferred(Timing t)
{
    t.preferred = true;
    return t;
}

const Cap kCap{1920, 1080, 60};

bool picks(const std::vector<Timing> &modes, std::size_t index, Pick pick)
{
    const std::optional<Choice> choice = chooseMode(modes, kCap);
    return choice && choice->index == index && choice->pick == pick;
}

void modeTests()
{
    check("refresh 60.000", refreshMilliHz(k1080p60) == 60000);
    check("refresh 59.940", refreshMilliHz(k1080p5994) == 59940);
    check("refresh 50.000", refreshMilliHz(k1080p50) == 50000);
    check("refresh of an interlaced mode counts fields", refreshMilliHz(k1080i60) == 60000);
    check("refresh without totals", refreshMilliHz(Timing{}) == 0);
    check("mode name", modeName(k1080p5994) == "1920x1080@59.940");

    check("cap parsed", parseCap("1920x1080@60").has_value() && parseCap("1920x1080@60")->hz == 60);
    for (const char *bad : {"", "1920x1080", "1920x1080@", "x1080@60", "1920x1080@60Hz", "0x0@60", "1920x1080@60 ",
                            "1920X1080@60", "-1920x1080@60", "1920x1080@0", "99999x1080@60"})
        check(std::string("cap refused: '") + bad + "'", !parseCap(bad));

    // The plan's display matrix.
    check("4K preferred and 1080p60: 1080p60", picks({preferred(k2160p60), k1440p60, k1080p60, k720p60}, 2, Pick::Target));
    check("1080p at 50, 59.94 and 60: 60.000", picks({preferred(k1080p50), k1080p5994, k1080p60}, 2, Pick::Target));
    check("1080p at 59.94 and 50 only: 59.940", picks({k1080p50, k1080p5994}, 1, Pick::Target));
    Timing edid5994 = k1080p5994;
    edid5994.clock = 148350; // an EDID's 10 kHz step: 59.939 Hz
    check("an EDID's 59.94 counts as the target", picks({k1080p50, edid5994}, 1, Pick::Target));
    check("no 1080p: 720p60, a fallback", picks({preferred(k2160p60), k720p60}, 1, Pick::Fallback));
    check("only above the cap: the preferred mode", picks({k2160p60, preferred(k1440p60)}, 1, Pick::AboveCap));
    check("only above the cap, none preferred: the first", picks({k2160p60, k1440p60}, 0, Pick::AboveCap));
    check("720p60 before 1080p50", picks({k1080p50, k720p60}, 1, Pick::Fallback));
    check("1080p50 before 720p50", picks({k720p50, k1080p50}, 1, Pick::Fallback));
    check("interlaced never: 720p50", picks({k1080i60, k720p50}, 1, Pick::Fallback));
    check("1080p120 is not the target", picks({k1080p120, k1080p60}, 1, Pick::Target));
    check("equal modes: the preferred one", picks({k1080p60, preferred(k1080p60)}, 1, Pick::Target));
    check("equal modes, none preferred: the first", picks({k1080p60, k1080p60}, 0, Pick::Target));
    check("only interlaced: nothing", !chooseMode({k1080i60}, kCap));
    check("no modes: nothing", !chooseMode({}, kCap));

    const std::optional<std::string> line = modeline(k1080p60);
    check("modeline of 1080p60", line && *line == "148.500 1920 2008 2052 2200 1080 1084 1089 1125 +hsync +vsync");
    check("modeline keeps the polarities", modeline(k1440p60) && contains(*modeline(k1440p60), "+hsync -vsync"));
    check("no modeline without a polarity", !modeline(mode(148500, 1920, 2008, 2052, 2200, 1080, 1084, 1089, 1125,
                                                            false, kFlagPHSync)));
    check("no modeline for an interlaced mode", !modeline(k1080i60));

    // Every clock up to 2 GHz reads back exact through Qt's own parsing
    // (parseModeline in qkmsdevice.cpp: sscanf into a float and shorts,
    // then `fclock * 1000` into an integer field).
    int wrong = 0;
    std::uint32_t firstWrong = 0;
    for (std::uint32_t khz = 1; khz <= 2000000; ++khz) {
        Timing t = k1080p60;
        t.clock = khz;
        const std::optional<std::string> text = modeline(t);
        float fclock = 0;
        short h[4], v[4];
        char hsync[16], vsync[16];
        const bool parsed = text && std::sscanf(text->c_str(), "%f %hd %hd %hd %hd %hd %hd %hd %hd %15s %15s", &fclock,
                                                &h[0], &h[1], &h[2], &h[3], &v[0], &v[1], &v[2], &v[3], hsync, vsync) == 11;
        const std::uint32_t clock = parsed ? std::uint32_t(fclock * 1000) : 0;
        if (!parsed || clock != khz || h[0] != 1920 || h[3] != 2200 || v[0] != 1080 || v[3] != 1125) {
            if (!wrong++)
                firstWrong = khz;
        }
    }
    check("every clock to 2 GHz reads back exact (first wrong: " + std::to_string(firstWrong) + " kHz)", wrong == 0);
}

} // namespace

int main()
{
    deviceTests();
    modeTests();
    std::printf("display rules: %d passed, %d failed\n", g_passed, g_failed);
    return g_failed ? EXIT_FAILURE : EXIT_SUCCESS;
}
