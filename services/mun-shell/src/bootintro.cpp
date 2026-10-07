#include "bootintro.h"

#include "blur.h"
#include "bootintroshapes.h"

#include <QElapsedTimer>
#include <QFile>
#include <QLinearGradient>
#include <QPainter>
#include <QQuickWindow>
#include <QRadialGradient>
#include <QTransform>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <map>
#include <memory>

namespace {

using namespace bootshapes;

// The design's frame time leads the sound's by this much.
constexpr qreal kLead = 0.35;
// The design's view, 1414 x 900 units from (176, -22), is 1180 px wide at
// the canvas's centre.
constexpr qreal kView = 1180.0 / 1414.0;

qreal cl(qreal v) { return std::clamp<qreal>(v, 0, 1); }
qreal sp(qreal t, qreal a, qreal b) { return cl((t - a) / (b - a)); }
qreal io(qreal u) { return u < 0.5 ? 4 * u * u * u : 1 - std::pow(-2 * u + 2, 3) / 2; }
qreal o3(qreal u) { return 1 - std::pow(1 - u, 3); }

// Canvas <- view.
QTransform viewTransform()
{
    QTransform m;
    m.translate(960 - 590, 540 - 900 * kView / 2);
    m.scale(kView, kView);
    m.translate(-176, 22);
    return m;
}

// View <- the logo's own units (it sits 170 units higher, at 81 % of its
// drawn size, scaled from the base of the U).
QTransform logoTransform()
{
    QTransform m;
    m.translate(0, -170);
    m.translate(850, 670.5);
    m.scale(0.81, 0.81);
    m.translate(-850, -670.5);
    return m;
}

// View <- the LED's own units, centred on it.
QTransform ledTransform() { return QTransform::fromTranslate(850, 762.5); }

// The silhouette's place in the view, and the light behind it.
const QRectF kSilhouette(142.0, 470.4, 1436.4, 353.1);
const QPointF kBacklight(142.0 + 1436.4 / 2, 470.4 + 353.1 * 0.34);
// The moon's centre (the eclipse's shadow starts there) in the logo's units,
// the direction the shadow withdraws in, and the LED in the same units (the
// discharge spreads from it).
constexpr qreal kMoonX = 1107.0, kMoonY = 614.9, kLight = -0.9645;
constexpr qreal kLedX = 850.0, kLedY = 994.0;

// Deterministic chance: a number in [0, 1) for draw `n` of the frame at
// design time `s` (frames of 1/240 s).
quint64 mix64(quint64 x)
{
    x += 0x9E3779B97F4A7C15ULL;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL;
    return x ^ (x >> 31);
}
qreal chance(qreal s, quint64 n)
{
    const quint64 frame = quint64(std::llround(s * 240)) + 1;
    return qreal(mix64(frame * 0x100000001B3ULL ^ mix64(n)) >> 11) * (1.0 / 9007199254740992.0);
}

// ------------------------------------------------------------ shapes

// Path data of absolute M, L, C and Z, kept as numbers so that the points
// can be moved before the path is built.
struct Shape {
    struct Op {
        char op;
        int first;
        int count;
    };
    std::vector<Op> ops;
    std::vector<double> nums;
};

Shape parseShape(const char *d)
{
    Shape shape;
    const char *at = d;
    while (*at) {
        if (std::strchr("MLCZ", *at)) {
            shape.ops.push_back({*at, int(shape.nums.size()), 0});
            ++at;
        } else if (*at == '-' || *at == '.' || (*at >= '0' && *at <= '9')) {
            char *end = nullptr;
            shape.nums.push_back(std::strtod(at, &end));
            if (!shape.ops.empty())
                ++shape.ops.back().count;
            at = end;
        } else {
            ++at;
        }
    }
    return shape;
}

// Builds the path, every point (end and control points alike) passed
// through `moved(x, y, index)`.
template <typename Moved>
QPainterPath buildPath(const Shape &shape, Moved &&moved, Qt::FillRule rule)
{
    QPainterPath path;
    path.setFillRule(rule);
    int index = 0;
    auto point = [&](int at) { return moved(shape.nums[size_t(at)], shape.nums[size_t(at + 1)], index++); };
    for (const Shape::Op &op : shape.ops) {
        int at = op.first;
        const int end = op.first + op.count;
        switch (op.op) {
        case 'M':
            if (at + 1 < end) {
                path.moveTo(point(at));
                at += 2;
            }
            [[fallthrough]];
        case 'L':
            for (; at + 1 < end; at += 2)
                path.lineTo(point(at));
            break;
        case 'C':
            for (; at + 5 < end; at += 6) {
                const QPointF c1 = point(at), c2 = point(at + 2), p = point(at + 4);
                path.cubicTo(c1, c2, p);
            }
            break;
        case 'Z':
            path.closeSubpath();
            break;
        }
    }
    return path;
}

const Shape &letterShape()
{
    static const Shape shape = parseShape(kLetters);
    return shape;
}
const Shape &arcShape()
{
    static const Shape shape = parseShape(kArc);
    return shape;
}

// The discharge: points near its front, which runs out from the LED, are
// shaken, less as it fades.
QPainterPath discharged(const Shape &shape, qreal s, qreal front, qreal amp, Qt::FillRule rule, quint64 salt)
{
    if (amp <= 0)
        return buildPath(shape, [](double x, double y, int) { return QPointF(x, y); }, rule);
    return buildPath(shape, [&](double x, double y, int i) {
        const double d = std::hypot(x - kLedX, y - kLedY);
        const double e = std::exp(-std::pow((d - front) / 130, 2)) * amp;
        const double dy = e * (std::sin(d * 0.09 - s * 70) * 13 + (chance(s, salt + quint64(i)) - 0.5) * 9);
        const double dx = e * std::cos(d * 0.07 + s * 55) * 6;
        return QPointF(x + dx, y + dy);
    }, rule);
}

// The LED: each point of its oval paired with one on a circle of radius r0;
// at g = 0 it is that circle, at 1 the oval.
QPainterPath ledOutline(qreal g, qreal r0)
{
    constexpr size_t count = sizeof(kLedOutline) / sizeof(kLedOutline[0]);
    static const std::array<double, count> angles = [] {
        std::array<double, count> a{};
        for (size_t i = 0; i < count; ++i)
            a[i] = std::atan2(kLedOutline[i][1] / kLedHalfHeight, kLedOutline[i][0] / kLedHalfWidth);
        return a;
    }();
    QPainterPath path;
    for (size_t i = 0; i < count; ++i) {
        const double cx = r0 * std::cos(angles[i]), cy = r0 * std::sin(angles[i]);
        const QPointF p(cx + (kLedOutline[i][0] - cx) * g, cy + (kLedOutline[i][1] - cy) * g);
        if (i == 0)
            path.moveTo(p);
        else
            path.lineTo(p);
    }
    path.closeSubpath();
    return path;
}

QImage loadImage(const char *name)
{
    QImage image(QStringLiteral(":/qt/qml/MUN/Shell/images/%1.png").arg(QLatin1String(name)));
    return image.convertToFormat(QImage::Format_ARGB32_Premultiplied);
}

// ------------------------------------------------------------ pixels

// Triangular dither, -1 to 1 levels, fixed for each pixel: dark gradients
// stepped to 8 bits show rings that a screen makes plain; this hides them,
// as a browser does, and the same pixel always gets the same value.
double dither(int x, int y)
{
    const quint64 h = mix64((quint64(quint32(x)) << 32) ^ quint32(y));
    return double(h & 0xffff) / 65536.0 - double((h >> 16) & 0xffff) / 65536.0;
}

// An elliptical gradient's position, 0 at `centre` to 1 at the radii, for
// the device pixel (x, y) of an image at `scale` over the canvas.
double ellipse(int x, int y, qreal scale, QPointF centre, qreal rx, qreal ry)
{
    const double dx = ((x + 0.5) / scale - centre.x()) / rx, dy = ((y + 0.5) / scale - centre.y()) / ry;
    return std::sqrt(dx * dx + dy * dy);
}

QImage transparent(const QSize &size, qreal scale)
{
    QImage image(size, QImage::Format_ARGB32_Premultiplied);
    image.fill(Qt::transparent);
    image.setDevicePixelRatio(scale);
    return image;
}

// Brightness, then hue-rotate, then saturate, as CSS filters compose them,
// on premultiplied pixels (the matrices are linear, so alpha factors out).
void colourFilter(QImage &image, qreal brightness, qreal hueDegrees = 0, qreal saturate = 1)
{
    const double c = std::cos(hueDegrees * M_PI / 180), s = std::sin(hueDegrees * M_PI / 180);
    const double h[3][3] = {
        {0.213 + c * 0.787 - s * 0.213, 0.715 - c * 0.715 - s * 0.715, 0.072 - c * 0.072 + s * 0.928},
        {0.213 - c * 0.213 + s * 0.143, 0.715 + c * 0.285 + s * 0.140, 0.072 - c * 0.072 - s * 0.283},
        {0.213 - c * 0.213 - s * 0.787, 0.715 - c * 0.715 + s * 0.715, 0.072 + c * 0.928 + s * 0.072}};
    const double t = saturate;
    const double a[3][3] = {
        {0.213 + 0.787 * t, 0.715 - 0.715 * t, 0.072 - 0.072 * t},
        {0.213 - 0.213 * t, 0.715 + 0.285 * t, 0.072 - 0.072 * t},
        {0.213 - 0.213 * t, 0.715 - 0.715 * t, 0.072 + 0.928 * t}};
    double m[3][3];
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j)
            m[i][j] = (a[i][0] * h[0][j] + a[i][1] * h[1][j] + a[i][2] * h[2][j]) * brightness;
    for (int y = 0; y < image.height(); ++y) {
        QRgb *line = reinterpret_cast<QRgb *>(image.scanLine(y));
        for (int x = 0; x < image.width(); ++x) {
            const QRgb p = line[x];
            const int alpha = qAlpha(p);
            if (!alpha)
                continue;
            const double r = qRed(p), g = qGreen(p), b = qBlue(p);
            auto channel = [&](int i) {
                return int(std::clamp(m[i][0] * r + m[i][1] * g + m[i][2] * b, 0.0, double(alpha)) + 0.5);
            };
            line[x] = qRgba(channel(0), channel(1), channel(2), alpha);
        }
    }
}

// Composites `blurred` copy under the image: SVG's glow (a Gaussian blur
// merged under its source). Wide blurs run at a lower resolution: a glow has
// no detail to lose.
void glow(QImage &image, qreal deviceSigma)
{
    const qreal factor = deviceSigma > 3 ? 2.0 : 1.0;
    QImage soft = factor > 1 ? image.scaled(image.size() / factor, Qt::IgnoreAspectRatio, Qt::SmoothTransformation)
                             : image.copy();
    soft.setDevicePixelRatio(1);
    blur::gaussian(soft, deviceSigma / factor);
    QImage out(image.size(), QImage::Format_ARGB32_Premultiplied);
    out.fill(Qt::transparent);
    {
        QPainter p(&out);
        p.setRenderHint(QPainter::SmoothPixmapTransform);
        p.drawImage(QRectF(QPointF(0, 0), QSizeF(image.size())), soft);
        QImage sharp = image;
        sharp.setDevicePixelRatio(1);
        p.drawImage(0, 0, sharp);
    }
    out.setDevicePixelRatio(image.devicePixelRatio());
    image = out;
}

// CSS drop-shadow(0 0 radius colour) of `image`, as an image of its own:
// the alpha blurred by radius / 2 and tinted.
QImage dropShadow(const QImage &image, qreal deviceRadius, const QColor &colour)
{
    const qreal sigma = deviceRadius / 2;
    const qreal factor = sigma > 6 ? 4.0 : sigma > 3 ? 2.0 : 1.0;
    QImage soft = image.scaled(image.size() / factor, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
    soft.setDevicePixelRatio(1);
    blur::gaussian(soft, sigma / factor);
    {
        QPainter p(&soft);
        p.setCompositionMode(QPainter::CompositionMode_SourceIn);
        p.fillRect(soft.rect(), colour);
    }
    QImage out(image.size(), QImage::Format_ARGB32_Premultiplied);
    out.fill(Qt::transparent);
    {
        QPainter p(&out);
        p.setRenderHint(QPainter::SmoothPixmapTransform);
        p.drawImage(QRectF(QPointF(0, 0), QSizeF(image.size())), soft);
    }
    out.setDevicePixelRatio(image.devicePixelRatio());
    return out;
}

// ------------------------------------------------------------ the LED's melt

// SVG's feTurbulence (fractalNoise, one octave), as the specification's
// reference code computes it, for the LED's melt.
class Turbulence {
public:
    explicit Turbulence(long seed)
    {
        seed = setupSeed(seed);
        int i = 0;
        for (int k = 0; k < 4; ++k) {
            for (i = 0; i < kSize; ++i) {
                m_lattice[i] = i;
                for (int j = 0; j < 2; ++j)
                    m_gradient[k][i][j] = double(((seed = random(seed)) % (kSize + kSize)) - kSize) / kSize;
                const double s = std::sqrt(m_gradient[k][i][0] * m_gradient[k][i][0] + m_gradient[k][i][1] * m_gradient[k][i][1]);
                m_gradient[k][i][0] /= s;
                m_gradient[k][i][1] /= s;
            }
        }
        while (--i) {
            const int k = m_lattice[i];
            const int j = int((seed = random(seed)) % kSize);
            m_lattice[i] = m_lattice[j];
            m_lattice[j] = k;
        }
        for (i = 0; i < kSize + 2; ++i) {
            m_lattice[kSize + i] = m_lattice[i];
            for (int k = 0; k < 4; ++k)
                for (int j = 0; j < 2; ++j)
                    m_gradient[k][kSize + i][j] = m_gradient[k][i][j];
        }
    }

    // fractalNoise's colour value, 0 to 1, of channel `c` at (x, y).
    double value(int c, double x, double y) const { return std::clamp((noise(c, x, y) + 1) / 2, 0.0, 1.0); }

private:
    static constexpr int kSize = 0x100, kMask = 0xff, kPerlin = 0x1000;
    static constexpr long kM = 2147483647, kA = 16807, kQ = 127773, kR = 2836;
    static long setupSeed(long seed)
    {
        if (seed <= 0)
            seed = -(seed % (kM - 1)) + 1;
        return std::min(seed, kM - 1);
    }
    static long random(long seed)
    {
        long result = kA * (seed % kQ) - kR * (seed / kQ);
        return result <= 0 ? result + kM : result;
    }
    double noise(int c, double x, double y) const
    {
        auto curve = [](double t) { return t * t * (3. - 2. * t); };
        auto lerp = [](double t, double a, double b) { return a + t * (b - a); };
        double t = x + kPerlin;
        const int bx0 = int(t) & kMask, bx1 = (bx0 + 1) & kMask;
        const double rx0 = t - int(t), rx1 = rx0 - 1.0;
        t = y + kPerlin;
        const int by0 = int(t) & kMask, by1 = (by0 + 1) & kMask;
        const double ry0 = t - int(t), ry1 = ry0 - 1.0;
        const int i = m_lattice[bx0], j = m_lattice[bx1];
        const int b00 = m_lattice[i + by0], b10 = m_lattice[j + by0], b01 = m_lattice[i + by1], b11 = m_lattice[j + by1];
        const double sx = curve(rx0), sy = curve(ry0);
        const double *q = m_gradient[c][b00];
        double u = rx0 * q[0] + ry0 * q[1];
        q = m_gradient[c][b10];
        double v = rx1 * q[0] + ry0 * q[1];
        const double a = lerp(sx, u, v);
        q = m_gradient[c][b01];
        u = rx0 * q[0] + ry1 * q[1];
        q = m_gradient[c][b11];
        v = rx1 * q[0] + ry1 * q[1];
        const double b = lerp(sx, u, v);
        return lerp(sy, a, b);
    }

    int m_lattice[kSize + kSize + 2];
    double m_gradient[4][kSize + kSize + 2][2];
};

const Turbulence &turbulence(int seed)
{
    static std::map<int, std::unique_ptr<Turbulence>> cache;
    auto &slot = cache[seed];
    if (!slot)
        slot = std::make_unique<Turbulence>(seed);
    return *slot;
}

// feDisplacementMap over feTurbulence (base frequency 0.035 x 0.22 in the
// LED's own units): each pixel takes the colour from `scale` units away as
// the noise's red and green say. `toLocal` maps the image's pixels to those
// units.
void melt(QImage &image, const QTransform &toLocal, qreal scale, int seed)
{
    if (scale <= 0)
        return;
    const Turbulence &noise = turbulence(seed);
    const QImage source = image.copy();
    const QTransform fromLocal = toLocal.inverted();
    const int w = image.width(), h = image.height();
    for (int y = 0; y < h; ++y) {
        QRgb *line = reinterpret_cast<QRgb *>(image.scanLine(y));
        for (int x = 0; x < w; ++x) {
            const QPointF local = toLocal.map(QPointF(x + 0.5, y + 0.5));
            const double dx = scale * (noise.value(0, local.x() * 0.035, local.y() * 0.22) - 0.5);
            const double dy = scale * (noise.value(1, local.x() * 0.035, local.y() * 0.22) - 0.5);
            const QPointF from = fromLocal.map(local + QPointF(dx, dy)) - QPointF(0.5, 0.5);
            const int fx = int(std::floor(from.x())), fy = int(std::floor(from.y()));
            const double ax = from.x() - fx, ay = from.y() - fy;
            double acc[4] = {0, 0, 0, 0};
            for (int k = 0; k < 4; ++k) {
                const int sx = fx + (k & 1), sy = fy + (k >> 1);
                if (sx < 0 || sy < 0 || sx >= w || sy >= h)
                    continue;
                const double weight = ((k & 1) ? ax : 1 - ax) * ((k >> 1) ? ay : 1 - ay);
                const QRgb p = reinterpret_cast<const QRgb *>(source.constScanLine(sy))[sx];
                acc[0] += weight * qRed(p);
                acc[1] += weight * qGreen(p);
                acc[2] += weight * qBlue(p);
                acc[3] += weight * qAlpha(p);
            }
            line[x] = qRgba(int(acc[0] + 0.5), int(acc[1] + 0.5), int(acc[2] + 0.5), int(acc[3] + 0.5));
        }
    }
}

} // namespace

// ------------------------------------------------------------ the item

BootIntro::BootIntro(QQuickItem *parent) : QQuickPaintedItem(parent)
{
    setAntialiasing(true);
}

void BootIntro::setPart(Part part)
{
    if (part == m_part)
        return;
    m_part = part;
    m_still = Layer();
    m_finished = Layer();
    emit partChanged();
    refresh();
}

void BootIntro::setT(qreal t)
{
    if (qFuzzyCompare(t, m_t))
        return;
    m_t = t;
    emit tChanged();
    // The silhouette's and the engraving's still layers are made as the
    // start-up begins (the screen still black), not on the hit's frame.
    if (t >= 0 && (m_part == Silhouette || m_part == Engraving) && m_still.image.isNull() && window())
        prepare(window()->effectiveDevicePixelRatio());
    refresh();
}

void BootIntro::setReduceMotion(bool on)
{
    if (on == m_reduceMotion)
        return;
    m_reduceMotion = on;
    m_finished = Layer();
    emit reduceMotionChanged();
    refresh();
}

void BootIntro::setBreath(qreal breath)
{
    if (qFuzzyCompare(breath, m_breath))
        return;
    m_breath = breath;
    emit breathChanged();
    refresh();
}

QRectF BootIntro::area() const
{
    const QTransform view = viewTransform();
    switch (m_part) {
    case Screen:
    case Tube:
        return QRectF(0, 0, 1920, 1080);
    case Silhouette: {
        const QRectF light(kBacklight.x() - 640, kBacklight.y() - 95, 1280, 190);
        return view.mapRect(kSilhouette.united(light)).adjusted(-16, -16, 16, 16).toAlignedRect();
    }
    case Engraving:
        return view.mapRect(kSilhouette).toAlignedRect();
    case Mark: {
        const QTransform logo = logoTransform() * view;
        const QRectF shapes = buildPath(letterShape(), [](double x, double y, int) { return QPointF(x, y); }, Qt::OddEvenFill)
                                  .boundingRect()
                                  .united(buildPath(arcShape(), [](double x, double y, int) { return QPointF(x, y); }, Qt::WindingFill)
                                              .boundingRect());
        // The tremor, the letters' arrival at 105 %, their glow and the
        // warm shadow around it all.
        return logo.mapRect(shapes).adjusted(-90, -90, 90, 90).toAlignedRect();
    }
    case Led:
        return (ledTransform() * view).mapRect(QRectF(-280, -150, 560, 300)).toAlignedRect();
    }
    return QRectF();
}

QPointF BootIntro::shake() const
{
    const qreal s = m_t + kLead;
    if (m_reduceMotion || s < 3.8)
        return QPointF();
    const qreal shake = 9 * std::exp(-(s - 3.8) * 9);
    return QPointF(std::sin(s * 90) * shake, std::cos(s * 77) * shake);
}

qreal BootIntro::flash() const
{
    const qreal s = m_t + kLead;
    return (s >= 3.8 ? 0.12 * std::exp(-(s - 3.8) * 6) : 0) + (s >= 7.0 ? 0.25 * std::exp(-(s - 7.0) * 6) : 0);
}

qreal BootIntro::fade() const
{
    return 1 - sp(m_t + kLead, 11.25, 12.1);
}

QByteArray BootIntro::stateKey() const
{
    const qreal s = m_t + kLead;
    const QByteArray frame = QByteArray::number(qint64(std::llround(s * 240)));
    switch (m_part) {
    case Screen:
    case Tube:
        return "still";
    case Silhouette: {
        const qreal f = s < 3.78 ? 0 : (s < 3.9 ? sp(s, 3.78, 3.9) : std::exp(-(s - 3.9) / 0.45));
        const qreal b = f * 0.38;
        return QByteArray::number(qint64(std::llround((b > 0.003 ? b : 0) * 2000)));
    }
    case Engraving:
        return s > 3.86 && s < 4.9 ? frame : "off";
    case Mark:
        if (s < 7.0)
            return "none";
        return s >= 10.35 ? "finished" : frame;
    case Led:
        if (m_breath >= 0)
            return "breath " + QByteArray::number(qint64(std::llround(m_breath * 1000)));
        if (s < 0.65)
            return "dark";
        if (s < 3.75 || (s >= 7.0 && s < 9.1) || (s >= 10.4 && s < 11.55))
            return frame;
        if (s < 7.0)
            return "lit";
        return s < 10.4 ? "warm" : "gone";
    }
    return QByteArray();
}

void BootIntro::refresh()
{
    const QByteArray key = stateKey();
    if (key == m_painted)
        return;
    m_painted = key;
    update();
}

void BootIntro::paint(QPainter *painter)
{
    const qreal scale = blur::deviceScale(painter, width());
    // The item's texture is a whole number of pixels, so its scale can be a
    // hair off the window's, which a layer made ahead of time used: such a
    // layer is taken as it is, one pixel to one, not made again.
    for (Layer *layer : {&m_still, &m_finished}) {
        if (!layer->image.isNull() && layer->scale != scale && std::abs(layer->scale - scale) < 0.01) {
            layer->image.setDevicePixelRatio(scale);
            layer->scale = scale;
        }
    }
    painter->setRenderHint(QPainter::Antialiasing);
    painter->setRenderHint(QPainter::SmoothPixmapTransform);
    QElapsedTimer took;
    took.start();
    switch (m_part) {
    case Screen: paintScreen(painter, scale); break;
    case Silhouette: paintSilhouette(painter, scale); break;
    case Engraving: paintEngraving(painter, scale); break;
    case Mark: paintMark(painter, scale); break;
    case Led: paintLed(painter, scale); break;
    case Tube: paintTube(painter, scale); break;
    }
    // The laboratory times this (MUN_SHELL_TIMING): it runs on the GUI thread
    // while the system loads.
    static const bool timing = qEnvironmentVariableIsSet("MUN_SHELL_TIMING");
    if (timing)
        qInfo("mun-shell: start-up part %d at %.3f s painted in %.2f ms", int(m_part), double(m_t),
              double(took.nsecsElapsed()) / 1e6);
}

QImage BootIntro::offscreen(qreal scale) const
{
    return transparent(QSize(int(std::ceil(width() * scale)), int(std::ceil(height() * scale))), scale);
}

// A painter on `image` drawing in canvas coordinates.
namespace {
void toCanvas(QPainter &p, const QRectF &area)
{
    p.setRenderHint(QPainter::Antialiasing);
    p.setRenderHint(QPainter::SmoothPixmapTransform);
    p.translate(-area.topLeft());
}
} // namespace

void BootIntro::paintScreen(QPainter *painter, qreal scale)
{
    // radial-gradient(80% 75% at 50% 48%, #0D0E11, #050506 70%, #000).
    if (m_still.image.isNull() || m_still.scale != scale) {
        // Computed pixel by pixel and dithered: QPainter's gradients step.
        m_still.image = offscreen(scale);
        m_still.scale = scale;
        const double stops[3][4] = {{0, 13, 14, 17}, {0.7, 5, 5, 6}, {1, 0, 0, 0}};
        for (int y = 0; y < m_still.image.height(); ++y) {
            QRgb *line = reinterpret_cast<QRgb *>(m_still.image.scanLine(y));
            for (int x = 0; x < m_still.image.width(); ++x) {
                const double d = std::min(1.0, ellipse(x, y, scale, QPointF(960, 1080 * 0.48), 0.8 * 1920, 0.75 * 1080));
                const int i = d < 0.7 ? 0 : 1;
                const double f = (d - stops[i][0]) / (stops[i + 1][0] - stops[i][0]), n = dither(x, y);
                auto channel = [&](int c) {
                    return std::clamp(int(std::lround(stops[i][c] + (stops[i + 1][c] - stops[i][c]) * f + n)), 0, 255);
                };
                line[x] = qRgb(channel(1), channel(2), channel(3));
            }
        }
    }
    painter->drawImage(QPointF(0, 0), m_still.image);
}

void BootIntro::paintTube(QPainter *painter, qreal scale)
{
    // The tube's lines, 1 px of black at 28 % every 3 px, over a vignette:
    // radial-gradient(120% 100%, transparent 60%, rgba(0, 0, 0, .65)).
    if (m_still.image.isNull() || m_still.scale != scale) {
        m_still.image = offscreen(scale);
        m_still.scale = scale;
        for (int y = 0; y < m_still.image.height(); ++y) {
            QRgb *line = reinterpret_cast<QRgb *>(m_still.image.scanLine(y));
            for (int x = 0; x < m_still.image.width(); ++x) {
                const double d = std::min(1.0, ellipse(x, y, scale, QPointF(960, 540), 1.2 * 1920, 1080));
                const double alpha = d <= 0.6 ? 0 : 0.65 * 255 * (d - 0.6) / 0.4;
                line[x] = qRgba(0, 0, 0, std::clamp(int(std::lround(alpha + (alpha > 0 ? dither(x, y) : 0))), 0, 255));
            }
        }
        QPainter p(&m_still.image);
        for (int y = 0; y < 1080; y += 3)
            p.fillRect(QRectF(0, y, 1920, 1), QColor(0, 0, 0, int(0.28 * 255 + 0.5)));
    }
    painter->drawImage(QPointF(0, 0), m_still.image);
}

void BootIntro::prepare(qreal scale)
{
    const QRectF area = this->area();
    if (m_part == Silhouette) {
        QImage content = offscreen(scale);
        {
            QPainter p(&content);
            toCanvas(p, area);
            p.setTransform(viewTransform(), true);
            // The light behind the lid: an ellipse 1280 x 190 units.
            QRadialGradient g(QPointF(0.5, 0.5), 0.5);
            g.setCoordinateMode(QGradient::ObjectMode);
            g.setColorAt(0, QColor::fromRgbF(0xDC / 255., 0xE6 / 255., 0xFA / 255., 0.38));
            g.setColorAt(0.45, QColor::fromRgbF(0x8F / 255., 0xA6 / 255., 0xD6 / 255., 0.14));
            g.setColorAt(1, QColor::fromRgbF(0x40 / 255., 0x50 / 255., 0x70 / 255., 0));
            p.setPen(Qt::NoPen);
            p.setBrush(g);
            p.drawEllipse(kBacklight, 640, 95);
            p.drawImage(kSilhouette, loadImage("boot-silhouette"));
        }
        // The picture's warm shadow, as it is before the logo arrives.
        m_still.image = dropShadow(content, 8 * scale, QColor::fromRgbF(1, 190 / 255., 120 / 255., 0.12));
        QPainter p(&m_still.image);
        p.drawImage(QPointF(0, 0), content);
    } else if (m_part == Engraving) {
        m_still.image = offscreen(scale);
        QPainter p(&m_still.image);
        toCanvas(p, area);
        p.setTransform(viewTransform(), true);
        p.drawImage(kSilhouette, loadImage("boot-engraving"));
    }
    m_still.scale = scale;
}

void BootIntro::paintSilhouette(QPainter *painter, qreal scale)
{
    const qreal s = m_t + kLead;
    const qreal f = s < 3.78 ? 0 : (s < 3.9 ? sp(s, 3.78, 3.9) : std::exp(-(s - 3.9) / 0.45));
    const qreal opacity = f * 0.38 > 0.003 ? f * 0.38 : 0;
    if (opacity <= 0)
        return;
    if (m_still.image.isNull() || m_still.scale != scale)
        prepare(scale);
    painter->setOpacity(opacity);
    painter->drawImage(QPointF(0, 0), m_still.image);
}

void BootIntro::paintEngraving(QPainter *painter, qreal scale)
{
    const qreal s = m_t + kLead;
    const qreal f = s < 3.78 ? 0 : (s < 3.9 ? sp(s, 3.78, 3.9) : std::exp(-(s - 3.9) / 0.45));
    const qreal eg = sp(s, 3.86, 4.3);
    const qreal opacity = eg > 0 && s < 4.9 ? std::min<qreal>(1, f * 1.6 + 0.2) * (1 - sp(s, 4.3, 4.9)) : 0;
    if (opacity <= 0)
        return;
    if (m_still.image.isNull() || m_still.scale != scale)
        prepare(scale);
    // Through a band of light 300 units wide crossing the dome.
    QImage band = m_still.image.copy();
    {
        QPainter p(&band);
        toCanvas(p, area());
        p.setTransform(viewTransform(), true);
        const qreal ex = 667.2 - 150 + (1047.5 - 667.2 + 300) * eg;
        QLinearGradient g(ex - 150, 0, ex + 150, 0);
        g.setColorAt(0, QColor(0, 0, 0, 0));
        g.setColorAt(0.5, QColor(0, 0, 0, 255));
        g.setColorAt(1, QColor(0, 0, 0, 0));
        p.setCompositionMode(QPainter::CompositionMode_DestinationIn);
        p.fillRect(QRectF(-3000, -3000, 8000, 8000), g);
    }
    painter->setOpacity(opacity);
    painter->drawImage(QPointF(0, 0), band);
}

void BootIntro::paintMark(QPainter *painter, qreal scale)
{
    const qreal s = m_t + kLead;
    if (s < 7.0)
        return;
    const bool finished = s >= 10.35;
    if (finished && !m_finished.image.isNull() && m_finished.scale == scale) {
        painter->drawImage(QPointF(0, 0), m_finished.image);
        return;
    }
    const qreal at = finished ? 10.35 : s;
    const QRectF area = this->area();
    const QTransform logo = logoTransform() * viewTransform();
    const qreal sigma = 6 * kView * 0.81 * scale;   // the glow's, in device pixels

    // The discharge, from the LED outwards, over 2.3 s.
    const qreal dz = at - 7.0;
    const bool zapping = !m_reduceMotion && dz > 0 && dz < 2.3;
    const qreal front = 30 + 1800 * (1 - std::exp(-dz * 1.5));
    const qreal amp = zapping ? 1 - sp(dz, 0.4, 2.3) : 0;

    QLinearGradient silver(0, 0, 0, 1);
    silver.setCoordinateMode(QGradient::ObjectMode);
    silver.setColorAt(0, QColor("#FFFFFF"));
    silver.setColorAt(0.55, QColor("#E2DFD8"));
    silver.setColorAt(1, QColor("#A9A6A0"));
    QLinearGradient blood(0, 0, 1, 1);
    blood.setCoordinateMode(QGradient::ObjectMode);
    blood.setColorAt(0, QColor("#B4623A"));
    blood.setColorAt(1, QColor("#6E2A16"));

    QImage content = offscreen(scale);
    QPainter out(&content);
    const QRect whole = content.rect();

    // One shape filled into a layer of its own, only as large as it and its
    // glow, glowing; `mask` (drawing in the logo's units) may then cut it.
    auto element = [&](const QPainterPath &path, const QBrush &brush, const QTransform &extra, qreal opacity,
                       const std::function<void(QPainter &)> &mask) {
        const QTransform place = extra * logo;
        const QRectF box = place.mapRect(path.boundingRect());
        const qreal margin = 3 * sigma + 2;
        const QRect pixels = QRectF((box.topLeft() - area.topLeft()) * scale, box.size() * scale)
                                 .adjusted(-margin, -margin, margin, margin).toAlignedRect() & whole;
        if (pixels.isEmpty())
            return;
        const QPointF origin = QPointF(pixels.topLeft()) / scale + area.topLeft();   // in the canvas
        QImage image = transparent(pixels.size(), scale);
        {
            QPainter p(&image);
            p.setRenderHint(QPainter::Antialiasing);
            p.translate(-origin);
            p.setTransform(place, true);
            p.setPen(Qt::NoPen);
            p.setBrush(brush);
            p.drawPath(path);
        }
        glow(image, sigma);
        if (mask) {
            QPainter p(&image);
            p.translate(-origin);
            p.setTransform(logo, true);
            p.setCompositionMode(QPainter::CompositionMode_DestinationIn);
            mask(p);
        }
        out.setOpacity(opacity);
        out.drawImage(QPointF(pixels.topLeft()) / scale, image);
    };

    // The copper moon of the eclipse, then fading.
    const qreal eb = sp(at, 8.2, 8.45);
    const qreal ghost = 0.62 * eb * (1 - sp(at, 9.7, 10.35));
    if (ghost > 0)
        element(discharged(arcShape(), at, front, amp, Qt::WindingFill, 1u << 20), blood, QTransform(), ghost, nullptr);
    // The silver arc, the eclipse's shadow withdrawing from it.
    if (at >= 8.4) {
        std::function<void(QPainter &)> shadow;
        if (at < 10.25) {
            const qreal ew = sp(at, 8.4, 10.25), ee = io(ew);
            const qreal du = 60 + 600 * (ew * 0.75 + ee * 0.25);
            const QPointF umbra(kMoonX - du * std::cos(kLight), kMoonY - du * std::sin(kLight));
            shadow = [umbra](QPainter &p) {
                QRadialGradient g(umbra, 760);
                g.setColorAt(0, QColor(0, 0, 0, 0));
                g.setColorAt(0.8, QColor(0, 0, 0, 0));
                g.setColorAt(0.93, QColor(0, 0, 0, 102));
                g.setColorAt(1, QColor(0, 0, 0, 255));
                p.fillRect(QRectF(-3000, -3000, 8000, 8000), g);
            };
        }
        element(discharged(arcShape(), at, front, amp, Qt::WindingFill, 2u << 20), silver, QTransform(), 1, shadow);
    }
    // The letters fall into place with weight, from 105 %.
    const qreal pu = o3(sp(at, 7.0, 7.7));
    if (pu > 0) {
        static const QPointF centre = buildPath(letterShape(), [](double x, double y, int) { return QPointF(x, y); },
                                                Qt::OddEvenFill).boundingRect().center();
        const qreal k = 1.05 - 0.05 * pu;
        QTransform grow;
        grow.translate(centre.x(), centre.y());
        grow.scale(k, k);
        grow.translate(-centre.x(), -centre.y());
        element(discharged(letterShape(), at, front, amp, Qt::OddEvenFill, 3u << 20), silver, grow, pu, nullptr);
    }
    out.end();
    // The discharge's flicker, for its first 0.6 s.
    if (zapping && dz < 0.6)
        colourFilter(content, chance(at, 7) < 0.5 ? 1.3 : 0.92);

    // The picture's warm shadow, growing to its full light.
    const qreal gl = sp(at, 7.0, 9.1);
    QImage frame = dropShadow(content, (8 + 22 * gl) * scale, QColor::fromRgbF(1, 190 / 255., 120 / 255., 0.12 + 0.2 * gl));
    {
        QPainter p(&frame);
        p.drawImage(QPointF(0, 0), content);
    }
    if (finished) {
        m_finished.image = frame;
        m_finished.scale = scale;
    }
    painter->drawImage(QPointF(0, 0), frame);
}

void BootIntro::paintLed(QPainter *painter, qreal scale)
{
    const qreal s = m_t + kLead;
    const bool waiting = m_breath >= 0;
    if (!waiting && s < 0.65)
        return;
    const QRectF area = this->area();
    const QTransform place = ledTransform() * viewTransform();

    // Its shape: a point that breathes once, then grows into the oval.
    const qreal gu = sp(s, 1.7, 3.65);
    const qreal grow = waiting ? 1 : (gu < 0.5 ? 8 * gu * gu * gu * gu : 1 - 8 * std::pow(1 - gu, 4));
    const qreal hold = !waiting && s < 1.7 ? 0.06 * std::sin(M_PI * sp(s, 0.85, 1.7)) : 0;
    const QPainterPath outline = ledOutline(grow, kLedSeedRadius * (1 + hold * 1.6));
    const qreal p = o3(sp(s, 0.65, 0.85));
    qreal glowAmount = waiting ? 1 : (s < 0.65 ? 0 : p * (0.75 + 0.25 * grow));
    const qreal fl = !waiting && s >= 7.0 ? 1 + 0.5 * std::exp(-(s - 7.0) * 4) : 1;
    const qreal mt = waiting ? 0 : sp(s, 10.4, 11.55);
    if (mt >= 1)
        return;

    // The melt: the LED sags, widens then narrows, flickers.
    QTransform sag;
    if (mt > 0) {
        sag.translate(0, 7 * mt * mt);
        sag.scale(1 + 0.22 * std::sin(M_PI * std::min<qreal>(1, mt * 1.4)) - 0.5 * mt * mt, 1 - 0.55 * mt);
    }
    const QTransform local = sag * place;
    const qreal unit = kView * scale;   // device pixels per LED unit, before the sag

    QImage group = offscreen(scale);
    {
        QPainter g(&group);
        toCanvas(g, area);
        g.setTransform(local, true);
        // Its light around it.
        blur::paintBlurredPath(&g, outline.boundingRect().adjusted(-60, -60, 60, 60), outline,
                               QColor::fromRgbF(1, 0x9A / 255., 0x2E / 255., 0.75), 14, unit);
        // The lens, its edge dark.
        QLinearGradient fill(0, 0, 1, 0);
        fill.setCoordinateMode(QGradient::ObjectMode);
        fill.setColorAt(0, QColor("#E9822A"));
        fill.setColorAt(0.5, QColor("#FFD9A2"));
        fill.setColorAt(1, QColor("#E9822A"));
        g.setBrush(fill);
        g.setPen(QPen(QColor("#2A1A0C"), 2.2, Qt::SolidLine, Qt::FlatCap, Qt::RoundJoin));
        g.drawPath(outline);
        // Two sweeps of light along it while it loads.
        const qreal h1 = sp(s, 1.5, 2.5), h2 = sp(s, 2.7, 3.7);
        const bool sweeping = !waiting && ((h1 > 0 && h1 < 1) || (h2 > 0 && h2 < 1));
        if (sweeping) {
            const qreal hv = h1 > 0 && h1 < 1 ? h1 : h2;
            const qreal cx = -kLedHalfWidth * 1.24 + kLedHalfWidth * 2.48 * io(hv);
            QPainterPath spot;
            spot.addEllipse(QPointF(cx, 0), 30, 13);
            g.setClipPath(outline);
            blur::paintBlurredPath(&g, spot.boundingRect().adjusted(-16, -16, 16, 16), spot,
                                   QColor::fromRgbF(1, 0xF3 / 255., 0xDC / 255., 0.9), 4, unit);
        }
    }
    if (mt > 0) {
        // Sinks into its own noise as it goes out, amber to ember.
        QTransform toLocal = local;
        toLocal *= QTransform::fromTranslate(-area.left(), -area.top());
        toLocal *= QTransform::fromScale(scale, scale);
        melt(group, toLocal.inverted(), 26 * std::sin(M_PI * std::min<qreal>(1, mt * 1.15)), int(std::floor(s * 30)) % 50);
        const qreal lum = mt < 0.15 ? 1 + 1.8 * (mt / 0.15) : 2.8 * std::pow(1 - (mt - 0.15) / 0.85, 1.6);
        colourFilter(group, lum, -22 * mt, 1 + 0.8 * mt);
        if (!m_reduceMotion && mt > 0.25 && chance(s, 11) < 0.25 + 0.4 * mt)
            glowAmount *= 0.55 + chance(s, 12) * 0.3;
    } else if (waiting) {
        colourFilter(group, 0.55 + 0.45 * m_breath);
        glowAmount = 0.25 + 0.6 * m_breath;
    } else if (fl != 1) {
        colourFilter(group, fl);
    }

    // At its opacity, with the picture's warm shadow.
    QImage faded = offscreen(scale);
    {
        QPainter f(&faded);
        f.setOpacity(glowAmount);
        f.drawImage(QPointF(0, 0), group);
    }
    const qreal gl = waiting ? 0 : sp(s, 7.0, 9.1);
    painter->drawImage(QPointF(0, 0), dropShadow(faded, (8 + 22 * gl) * scale,
                                                 QColor::fromRgbF(1, 190 / 255., 120 / 255., 0.12 + 0.2 * gl)));
    painter->drawImage(QPointF(0, 0), faded);
}
