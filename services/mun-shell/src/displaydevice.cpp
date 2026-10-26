#include "displaydevice.h"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <system_error>

namespace display {

namespace {

namespace fs = std::filesystem;

// The drivers of the framebuffers firmware leaves (the EFI GOP, a device
// tree's simple-framebuffer, VESA, Open Firmware). They show what the
// firmware set and nothing else, and go away when the real driver binds.
// sysfs names the platform driver, which is the device's name for most of
// them: simpledrm binds as "simple-framebuffer", efidrm as
// "efi-framebuffer", vesadrm as "vesa-framebuffer".
constexpr std::string_view kFirmwareDrivers[] = {"simple-framebuffer", "efi-framebuffer", "vesa-framebuffer",
                                                 "simpledrm", "efidrm", "vesadrm", "ofdrm"};

// EDID clocks come in 10 kHz steps, so a 59.94 Hz mode can compute to
// 59.939: the target rates are taken with this margin, in millihertz.
constexpr std::uint32_t kRateMargin = 30;

std::string firstLine(const fs::path &path)
{
    std::ifstream file(path);
    std::string line;
    std::getline(file, line);
    while (!line.empty() && (line.back() == '\r' || line.back() == ' ' || line.back() == '\t'))
        line.pop_back();
    return line;
}

std::optional<int> cardIndex(std::string_view name)
{
    if (name.substr(0, 4) != "card" || name.size() == 4)
        return std::nullopt;
    int index = 0;
    const char *end = name.data() + name.size();
    const auto [ptr, ec] = std::from_chars(name.data() + 4, end, index);
    if (ec != std::errc() || ptr != end)
        return std::nullopt;
    return index;
}

// "fb0", not "fbcon" (the console's class device).
bool isFramebuffer(std::string_view name)
{
    return name.size() > 2 && name.substr(0, 2) == "fb" &&
           name.find_first_not_of("0123456789", 2) == std::string_view::npos;
}

bool isFirmware(std::string_view driver)
{
    return std::find(std::begin(kFirmwareDrivers), std::end(kFirmwareDrivers), driver) != std::end(kFirmwareDrivers);
}

std::string cardSummary(const Card &card)
{
    std::string line = card.name + ": " + (card.driver.empty() ? std::string("driver unknown") : card.driver);
    if (card.firmware)
        line += " (firmware framebuffer)";
    if (card.connectors.empty())
        return line + ", no connectors";
    for (std::size_t i = 0; i < card.connectors.size(); ++i)
        line += (i ? ", " : "; ") + card.connectors[i].name + " " + card.connectors[i].status;
    return line;
}

const Card::Connector *connected(const Card &card)
{
    for (const Card::Connector &connector : card.connectors) {
        if (connector.status == "connected")
            return &connector;
    }
    return nullptr;
}

bool usableMode(const Timing &mode)
{
    return !(mode.flags & (kFlagInterlace | kFlagDoubleScan)) && mode.htotal && mode.vtotal;
}

// The index of the best mode by `better` among those `fits` accepts.
std::optional<std::size_t> best(const std::vector<Timing> &modes, const std::function<bool(const Timing &)> &fits,
                                const std::function<bool(const Timing &, const Timing &)> &better)
{
    std::optional<std::size_t> found;
    for (std::size_t i = 0; i < modes.size(); ++i) {
        if (usableMode(modes[i]) && fits(modes[i]) && (!found || better(modes[i], modes[*found])))
            found = i;
    }
    return found;
}

std::uint32_t distance(std::uint32_t a, std::uint32_t b)
{
    return a > b ? a - b : b - a;
}

// The pixel clock in MHz as Qt reads it back: sscanf("%f") into a float, then
// `fclock * 1000` stored in an integer field, which truncates
// (parseModeline, qkmsdevice.cpp). Three decimals lose a kilohertz for some
// clocks (148.352 MHz, the kernel's 1080p at 59.94 Hz, among them), so the
// float is stepped up until it reads back exact. The arithmetic is single
// precision on the console's arm64 as it is here.
std::optional<std::string> clockText(std::uint32_t khz)
{
    char text[32];
    std::snprintf(text, sizeof text, "%u.%03u", khz / 1000, khz % 1000);
    float value = std::strtof(text, nullptr);
    for (int step = 0; step < 8; ++step) {
        if (static_cast<std::uint32_t>(value * 1000) == khz)
            return std::string(text);
        value = std::nextafter(value, HUGE_VALF);
        // Nine significant digits read back as this very float.
        std::snprintf(text, sizeof text, "%.9g", double(value));
    }
    return std::nullopt;
}

} // namespace

std::vector<Card> scanCards(const fs::path &drm)
{
    std::vector<Card> cards;
    std::error_code error;
    std::vector<std::string> names;
    // The error_code forms throughout: sysfs can change under the scan.
    for (fs::directory_iterator entries(drm, error), end; !error && entries != end; entries.increment(error))
        names.push_back(entries->path().filename().string());
    std::sort(names.begin(), names.end());

    for (const std::string &name : names) {
        const std::optional<int> index = cardIndex(name);
        if (!index)
            continue;
        Card card;
        card.name = name;
        card.index = *index;
        const fs::path driver = fs::read_symlink(drm / name / "device" / "driver", error);
        if (!error)
            card.driver = driver.filename().string();
        error.clear();
        card.firmware = isFirmware(card.driver);
        const std::string prefix = name + "-";
        for (const std::string &entry : names) {
            if (entry.size() > prefix.size() && entry.compare(0, prefix.size(), prefix) == 0)
                card.connectors.push_back({entry.substr(prefix.size()), firstLine(drm / entry / "status")});
        }
        cards.push_back(std::move(card));
    }
    std::sort(cards.begin(), cards.end(), [](const Card &a, const Card &b) { return a.index < b.index; });
    return cards;
}

std::optional<Display> usableDisplay(const std::vector<Card> &cards)
{
    for (const Card &card : cards) {
        if (card.name != kCard || card.firmware)
            continue;
        if (const Card::Connector *connector = connected(card))
            return Display{card.name, connector->name, card.driver};
    }
    return std::nullopt;
}

std::vector<std::string> describe(const std::vector<Card> &cards, const fs::path &graphics)
{
    std::vector<std::string> lines;
    for (const Card &card : cards)
        lines.push_back(cardSummary(card));
    if (cards.empty())
        lines.push_back("no DRM device");

    std::error_code error;
    std::vector<std::string> framebuffers;
    for (fs::directory_iterator entries(graphics, error), end; !error && entries != end; entries.increment(error)) {
        const std::string name = entries->path().filename().string();
        if (isFramebuffer(name))
            framebuffers.push_back(name + ": " + firstLine(entries->path() / "name"));
    }
    std::sort(framebuffers.begin(), framebuffers.end());
    if (framebuffers.empty())
        framebuffers.push_back("no framebuffer device");
    lines.insert(lines.end(), framebuffers.begin(), framebuffers.end());

    const auto card0 = std::find_if(cards.begin(), cards.end(), [](const Card &card) { return card.name == kCard; });
    std::string why;
    if (card0 == cards.end())
        why = "there is no card0";
    else if (card0->firmware)
        why = "card0 is the firmware's framebuffer, which the real display driver has not replaced";
    else if (!connected(*card0))
        why = "card0 has no connected connector";
    else
        why = "card0 is usable";
    for (const Card &card : cards) {
        if (card.name != kCard && !card.firmware && connected(card)) {
            why += "; a display is connected on " + card.name + " (" + card.driver +
                   "), but the shell can only draw on card0 (Qt's framebuffer platform opens no other)";
            break;
        }
    }
    lines.push_back(why);
    return lines;
}

std::uint32_t refreshMilliHz(const Timing &mode)
{
    std::uint64_t numerator = std::uint64_t(mode.clock) * 1000000;
    std::uint64_t denominator = std::uint64_t(mode.htotal) * mode.vtotal;
    if (!denominator)
        return 0;
    if (mode.flags & kFlagInterlace)
        numerator *= 2;
    if (mode.flags & kFlagDoubleScan)
        denominator *= 2;
    return std::uint32_t((numerator + denominator / 2) / denominator);
}

std::string modeName(const Timing &mode)
{
    const std::uint32_t rate = refreshMilliHz(mode);
    char text[48];
    std::snprintf(text, sizeof text, "%ux%u@%u.%03u", unsigned(mode.hdisplay), unsigned(mode.vdisplay), rate / 1000,
                  rate % 1000);
    return text;
}

std::optional<Cap> parseCap(std::string_view text)
{
    Cap cap;
    const char *at = text.data();
    const char *end = text.data() + text.size();
    auto number = [&](int &value, char next) {
        const auto [ptr, ec] = std::from_chars(at, end, value);
        if (ec != std::errc() || ptr == at)
            return false;
        at = ptr;
        if (next) {
            if (at == end || *at != next)
                return false;
            ++at;
        }
        return true;
    };
    if (!number(cap.width, 'x') || !number(cap.height, '@') || !number(cap.hz, 0) || at != end)
        return std::nullopt;
    if (cap.width < 1 || cap.width > 16384 || cap.height < 1 || cap.height > 16384 || cap.hz < 1 || cap.hz > 1000)
        return std::nullopt;
    return cap;
}

std::optional<Choice> chooseMode(const std::vector<Timing> &modes, const Cap &cap)
{
    const std::uint32_t exact = std::uint32_t(cap.hz) * 1000;
    // 60 Hz television timings also come at 60/1.001.
    const std::uint32_t lowest = std::uint32_t(std::uint64_t(cap.hz) * 1000000 / 1001) - kRateMargin;
    const std::uint32_t highest = exact + kRateMargin;
    auto atRate = [&](const Timing &m) {
        const std::uint32_t rate = refreshMilliHz(m);
        return rate >= lowest && rate <= highest;
    };
    auto capSize = [&](const Timing &m) { return m.hdisplay == cap.width && m.vdisplay == cap.height; };
    auto within = [&](const Timing &m) {
        return m.hdisplay <= cap.width && m.vdisplay <= cap.height && refreshMilliHz(m) <= highest;
    };
    auto area = [](const Timing &m) { return std::uint32_t(m.hdisplay) * m.vdisplay; };
    // Ties go to the preferred mode, then to the earlier one (`best` keeps
    // the first of equals).
    auto closerToExact = [&](const Timing &a, const Timing &b) {
        const std::uint32_t da = distance(refreshMilliHz(a), exact), db = distance(refreshMilliHz(b), exact);
        return da != db ? da < db : a.preferred && !b.preferred;
    };
    auto largerThenCloser = [&](const Timing &a, const Timing &b) {
        return area(a) != area(b) ? area(a) > area(b) : closerToExact(a, b);
    };
    auto faster = [&](const Timing &a, const Timing &b) {
        const std::uint32_t ra = refreshMilliHz(a), rb = refreshMilliHz(b);
        return ra != rb ? ra > rb : a.preferred && !b.preferred;
    };
    auto largerThenFaster = [&](const Timing &a, const Timing &b) {
        return area(a) != area(b) ? area(a) > area(b) : faster(a, b);
    };

    if (auto i = best(modes, [&](const Timing &m) { return capSize(m) && atRate(m); }, closerToExact))
        return Choice{*i, Pick::Target};
    if (auto i = best(modes, [&](const Timing &m) { return within(m) && atRate(m); }, largerThenCloser))
        return Choice{*i, Pick::Fallback};
    if (auto i = best(modes, [&](const Timing &m) { return capSize(m) && refreshMilliHz(m) < lowest; }, faster))
        return Choice{*i, Pick::Fallback};
    if (auto i = best(modes, within, largerThenFaster))
        return Choice{*i, Pick::Fallback};
    if (auto i = best(modes, [](const Timing &m) { return m.preferred; }, [](const Timing &, const Timing &) { return false; }))
        return Choice{*i, Pick::AboveCap};
    if (auto i = best(modes, [](const Timing &) { return true; }, [](const Timing &, const Timing &) { return false; }))
        return Choice{*i, Pick::AboveCap};
    return std::nullopt;
}

std::optional<std::string> modeline(const Timing &mode)
{
    const std::uint32_t sync = kFlagPHSync | kFlagNHSync | kFlagPVSync | kFlagNVSync;
    const bool oneHSync = bool(mode.flags & kFlagPHSync) != bool(mode.flags & kFlagNHSync);
    const bool oneVSync = bool(mode.flags & kFlagPVSync) != bool(mode.flags & kFlagNVSync);
    if ((mode.flags & ~sync) || !oneHSync || !oneVSync || !mode.htotal || !mode.vtotal)
        return std::nullopt;
    const std::optional<std::string> clock = clockText(mode.clock);
    if (!clock)
        return std::nullopt;
    char text[160];
    std::snprintf(text, sizeof text, "%s %u %u %u %u %u %u %u %u %s %s", clock->c_str(), unsigned(mode.hdisplay),
                  unsigned(mode.hsyncStart), unsigned(mode.hsyncEnd), unsigned(mode.htotal), unsigned(mode.vdisplay),
                  unsigned(mode.vsyncStart), unsigned(mode.vsyncEnd), unsigned(mode.vtotal),
                  mode.flags & kFlagPHSync ? "+hsync" : "-hsync", mode.flags & kFlagPVSync ? "+vsync" : "-vsync");
    return std::string(text);
}

} // namespace display
