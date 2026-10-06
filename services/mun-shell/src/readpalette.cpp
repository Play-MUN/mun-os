#include "readpalette.h"

#include <QString>

#include <algorithm>
#include <array>
#include <tuple>
#include <utility>
#include <vector>

namespace {

constexpr int kCells = 64;
constexpr int kBrightness = 235;

struct Cell {
    int r, g, b;
};

// The image's pixels as 8-bit RGB over black, as the checker decodes them.
std::vector<Cell> pixels(const QImage &source)
{
    const int w = source.width(), h = source.height();
    std::vector<Cell> out(size_t(w) * size_t(h));
    const bool deep = source.depth() == 64 || source.format() == QImage::Format_Grayscale16;
    if (deep) {
        // A 16-bit sample keeps its high byte (the checker's rule), alpha too.
        const QImage image = source.convertToFormat(QImage::Format_RGBA64);
        for (int y = 0; y < h; ++y) {
            const auto *line = reinterpret_cast<const QRgba64 *>(image.constScanLine(y));
            for (int x = 0; x < w; ++x) {
                const QRgba64 p = line[x];
                const int a = p.alpha() >> 8;
                out[size_t(y) * w + x] = {((p.red() >> 8) * a + 127) / 255, ((p.green() >> 8) * a + 127) / 255,
                                          ((p.blue() >> 8) * a + 127) / 255};
            }
        }
        return out;
    }
    const QImage image = source.convertToFormat(QImage::Format_ARGB32);   // straight, not premultiplied
    for (int y = 0; y < h; ++y) {
        const auto *line = reinterpret_cast<const QRgb *>(image.constScanLine(y));
        for (int x = 0; x < w; ++x) {
            const QRgb p = line[x];
            const int a = qAlpha(p);
            out[size_t(y) * w + x] = {(qRed(p) * a + 127) / 255, (qGreen(p) * a + 127) / 255, (qBlue(p) * a + 127) / 255};
        }
    }
    return out;
}

// Cell i covers [floor(i*size/64), max(that + 1, floor((i+1)*size/64))).
std::array<std::pair<int, int>, kCells> spans(int size)
{
    std::array<std::pair<int, int>, kCells> result{};
    for (int i = 0; i < kCells; ++i) {
        const int start = i * size / kCells;
        result[size_t(i)] = {start, std::max(start + 1, (i + 1) * size / kCells)};
    }
    return result;
}

// The accent score of a vivid warm cell, or -1 (the checker's _warm).
int warm(const Cell &c)
{
    const int high = std::max({c.r, c.g, c.b}), low = std::min({c.r, c.g, c.b});
    const int spread = high - low;
    if (spread == 0 || 20 * spread <= 7 * high || high != c.r)
        return -1;
    if ((c.g >= c.b && 12 * (c.g - c.b) < 11 * spread) || (c.g < c.b && 2 * (c.b - c.g) < spread))
        return spread;
    return -1;
}

QString hex(int r, int g, int b)
{
    return QStringLiteral("#%1%2%3").arg(r, 2, 16, QLatin1Char('0')).arg(g, 2, 16, QLatin1Char('0'))
        .arg(b, 2, 16, QLatin1Char('0')).toUpper();
}

} // namespace

QVariantMap readPalette(const QImage &cover)
{
    if (cover.isNull() || cover.width() < 1 || cover.height() < 1)
        return {};
    const int w = cover.width(), h = cover.height();
    const std::vector<Cell> source = pixels(cover);
    const auto columns = spans(w), lines = spans(h);
    std::vector<Cell> cells;
    cells.reserve(kCells * kCells);
    for (const auto &[top, bottom] : lines) {
        for (const auto &[left, right] : columns) {
            long long total[3] = {0, 0, 0};
            const long long count = (long long)(bottom - top) * (right - left);
            for (int y = top; y < bottom; ++y) {
                for (int x = left; x < right; ++x) {
                    const Cell &p = source[size_t(y) * w + x];
                    total[0] += p.r;
                    total[1] += p.g;
                    total[2] += p.b;
                }
            }
            cells.push_back({int((2 * total[0] + count) / (2 * count)), int((2 * total[1] + count) / (2 * count)),
                             int((2 * total[2] + count) / (2 * count))});
        }
    }

    // The accent, in reading order, the first of the best on a tie.
    int best = 0;
    const Cell *accent = nullptr;
    for (const Cell &c : cells) {
        const int score = warm(c);
        if (score > best) {
            best = score;
            accent = &c;
        }
    }

    std::vector<Cell> ordered = cells;
    std::sort(ordered.begin(), ordered.end(), [](const Cell &a, const Cell &b) {
        const long long la = 2126LL * a.r + 7152LL * a.g + 722LL * a.b;
        const long long lb = 2126LL * b.r + 7152LL * b.g + 722LL * b.b;
        if (la != lb)
            return la < lb;
        if (a.r != b.r)
            return a.r < b.r;
        if (a.g != b.g)
            return a.g < b.g;
        return a.b < b.b;
    });
    const long long n = (long long)ordered.size();
    QVariantMap result;
    const std::array<std::tuple<const char *, int, int>, 5> bands{{
        {"light", 97, 100}, {"hi", 82, 95}, {"mid", 50, 70}, {"low", 20, 35}, {"deep", 0, 8}}};
    for (const auto &[name, from, to] : bands) {
        const long long start = n * from / 100, end = n * to / 100;
        const long long count = end - start;
        long long total[3] = {0, 0, 0};
        for (long long i = start; i < end; ++i) {
            total[0] += ordered[size_t(i)].r;
            total[1] += ordered[size_t(i)].g;
            total[2] += ordered[size_t(i)].b;
        }
        result.insert(QString::fromLatin1(name),
                      hex(int((2 * total[0] + count) / (2 * count)), int((2 * total[1] + count) / (2 * count)),
                          int((2 * total[2] + count) / (2 * count))));
    }
    if (accent) {
        const int top = std::max({accent->r, accent->g, accent->b});
        auto scale = [&](int v) { return std::min(255, (2 * v * kBrightness + top) / (2 * top)); };
        result.insert(QStringLiteral("accent"), hex(scale(accent->r), scale(accent->g), scale(accent->b)));
    }
    return result;
}
