// The contrast rule of MUN Shape (docs/shape.md, "Contrast"), as the card
// tool's checker has it (tools/mun-card/mun_card/shape.py): the shell
// verifies with the actual colours what the checker proved, and applies the
// same rule to colours only it sees (a lent or cover-read accent). The
// constants and the arithmetic are the checker's; tests/test_shape.py
// compares the constants of both.
#pragma once

#include <QColor>
#include <QString>

#include <algorithm>
#include <cmath>
#include <optional>
#include <utility>
#include <vector>

namespace contrast {

constexpr double kTextRatio = 4.5;
constexpr double kFocusRatio = 3.0;
constexpr double kRounding = 1.0 / 255;     // one step of 8-bit composition either way
constexpr double kGlassSheen = 0.06;        // glass lightens its plate by up to this
constexpr double kPaperGrain = 0.04;        // paper varies its plate by up to this
constexpr double kGlassMinOpacity = 0.5;

struct Rgb {
    double r = 0, g = 0, b = 0;   // sRGB-encoded, 0 to 1
};

inline Rgb fromColor(const QColor &colour)
{
    return {colour.red() / 255.0, colour.green() / 255.0, colour.blue() / 255.0};
}

inline double linear(double channel)
{
    return channel <= 0.04045 ? channel / 12.92 : std::pow((channel + 0.055) / 1.055, 2.4);
}

inline double luminance(const Rgb &c)
{
    return 0.2126 * linear(c.r) + 0.7152 * linear(c.g) + 0.0722 * linear(c.b);
}

inline double ratio(const Rgb &a, const Rgb &b)
{
    const double la = luminance(a), lb = luminance(b);
    return (std::max(la, lb) + 0.05) / (std::min(la, lb) + 0.05);
}

inline Rgb mix(const Rgb &c, const Rgb &towards, double amount)
{
    return {c.r + (towards.r - c.r) * amount, c.g + (towards.g - c.g) * amount, c.b + (towards.b - c.b) * amount};
}

// The colours a plate of this material can take ("plain": MUN's own plate).
inline std::vector<Rgb> vertices(const Rgb &plate, const QString &material)
{
    if (material == QLatin1String("glass"))
        return {plate, mix(plate, {1, 1, 1}, kGlassSheen)};
    if (material == QLatin1String("paper"))
        return {mix(plate, {0, 0, 0}, kPaperGrain), mix(plate, {1, 1, 1}, kPaperGrain)};
    return {plate};
}

// Luminance bounds of plate * opacity + world * (1 - opacity) for any world,
// any plate within `plates`' channel ranges and any opacity from `opacity`
// up: each composed channel is multilinear, so its extremes are at the
// bounds, and luminance grows with every channel.
inline std::pair<double, double> bounds(double opacity, const std::vector<Rgb> &plates)
{
    auto channel = [&](double Rgb::*part, bool high) {
        double least = 1, most = 0;
        for (const Rgb &p : plates) {
            least = std::min(least, p.*part);
            most = std::max(most, p.*part);
        }
        const double value = high ? 1 - opacity * (1 - most) + kRounding : opacity * least - kRounding;
        return std::clamp(value, 0.0, 1.0);
    };
    const Rgb low{channel(&Rgb::r, false), channel(&Rgb::g, false), channel(&Rgb::b, false)};
    const Rgb high{channel(&Rgb::r, true), channel(&Rgb::g, true), channel(&Rgb::b, true)};
    return {luminance(low), luminance(high)};
}

struct Foreground {
    Rgb colour;
    double ratio;
};

// Every foreground keeps its ratio against every composite the bounds allow,
// all of them on the same side of it.
inline bool proven(const std::vector<Foreground> &foregrounds, double opacity, const std::vector<Rgb> &plates)
{
    const auto [least, most] = bounds(opacity, plates);
    for (const Foreground &f : foregrounds) {
        const double own = luminance(f.colour);
        const bool lighter = own + 0.05 >= f.ratio * (most + 0.05);
        const bool darker = least + 0.05 >= f.ratio * (own + 0.05);
        if (!lighter && !darker)
            return false;
    }
    return true;
}

// The least 8-bit opacity at which text and focus hold on this plate over
// any world: 1 for solid and paper, never under kGlassMinOpacity for glass,
// from 0 for "plain". None if even an opaque plate does not hold.
inline std::optional<double> minimumOpacity(const Rgb &plate, const Rgb &text, const Rgb &focus, const QString &material)
{
    const std::vector<Foreground> foregrounds{{text, kTextRatio}, {focus, kFocusRatio}};
    const std::vector<Rgb> plates = vertices(plate, material);
    if (material == QLatin1String("solid") || material == QLatin1String("paper")) {
        if (proven(foregrounds, 1.0, plates))
            return 1.0;
        return std::nullopt;
    }
    const int first = material == QLatin1String("glass") ? int(std::ceil(kGlassMinOpacity * 255)) : 0;
    for (int step = first; step <= 255; ++step) {
        if (proven(foregrounds, step / 255.0, plates))
            return step / 255.0;
    }
    return std::nullopt;
}

} // namespace contrast
