#include "shapeworld.h"

#include "sceneclock.h"
#include "shapefront.h"

#include <QElapsedTimer>
#include <QFile>
#include <QHash>
#include <QImageReader>
#include <QLinearGradient>
#include <QMutexLocker>
#include <QPainter>
#include <QPainterPath>
#include <QPolygon>
#include <QQuickWindow>
#include <QRegion>
#include <QRegularExpression>
#include <QSGImageNode>
#include <QSGTexture>
#include <QTimer>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <numeric>
#include <random>
#include <utility>
#include <vector>

namespace {

constexpr qreal kTau = 6.283185307179586;
constexpr int kLayerMaxSide = 2048;   // backdrop, layer, light texture (docs/shape.md, "Files")
constexpr int kSpriteMaxSide = 256;
constexpr qint64 kMiB = 1024 * 1024;
const QRegularExpression kPackagePath(QStringLiteral("^[A-Za-z0-9][A-Za-z0-9._-]{0,127}(/[A-Za-z0-9][A-Za-z0-9._-]{0,127})*$"));

// The display's budget for everything a world adds (docs/shape.md,
// "Objectives"): 136 MiB at 1080p, 200 MiB at 1440p, in proportion beyond.
qint64 budgetFor(const QSize &device)
{
    const qint64 pixels = qint64(device.width()) * device.height();
    if (pixels <= 1920LL * 1080)
        return 136 * kMiB;
    if (pixels <= 2560LL * 1440)
        return 200 * kMiB;
    return qint64(200.0 * kMiB * pixels / (2560.0 * 1440));
}

QString packagePath(const QString &root, const QVariant &value)
{
    const QString relative = value.toString();
    return !root.isEmpty() && kPackagePath.match(relative).hasMatch() ? root + QLatin1Char('/') + relative : QString();
}

// What a PNG's header says: its size and the format Qt decodes it to.
struct Header {
    QSize size;
    QImage::Format format = QImage::Format_Invalid;
};

Header headerOf(const QString &path)
{
    QImageReader reader(path, "png");
    return {reader.size(), reader.imageFormat()};
}

// The bytes a QImage of `size` takes in `format` (its lines padded to 32 bits).
qint64 imageBytes(const QSize &size, QImage::Format format)
{
    const qint64 bits = QImage::toPixelFormat(format).bitsPerPixel();
    return (qint64(size.width()) * bits + 31) / 32 * 4 * size.height();
}

// What decoding an image takes while it is used: its decoded pixels, and the
// copy made of them for the premultiplied (or, for a backdrop, opaque) form
// it is scaled from, unless that conversion can be done in place (formats of
// 32 bits per pixel). A format the header does not tell is counted as the
// largest, 64 bits per pixel, and its copy.
qint64 scratchFor(const Header &header)
{
    if (!header.size.isValid())
        return 0;
    if (header.format == QImage::Format_Invalid)
        return imageBytes(header.size, QImage::Format_RGBA64) + imageBytes(header.size, QImage::Format_ARGB32);
    const qint64 decoded = imageBytes(header.size, header.format);
    const bool inPlace = QImage::toPixelFormat(header.format).bitsPerPixel() == 32;
    return decoded + (inPlace ? 0 : imageBytes(header.size, QImage::Format_ARGB32_Premultiplied));
}

// `image` in `format`, converted in place where Qt can (the same depth), the
// decoded copy dropped at once where it cannot.
QImage converted(QImage &&image, QImage::Format format)
{
    if (image.format() == format)
        return std::move(image);
    QImage result = std::move(image).convertToFormat(format);
    image = QImage();
    return result;
}

// What a sprite takes once prepared, in its three sizes and their mirrors
// (as WorldPainter::load makes them).
qint64 spriteBytes(const QSize &size, qreal scale)
{
    qint64 total = 0;
    for (const qreal s : {0.8, 1.0, 1.2})
        total += 2 * imageBytes(QSize(std::max(1, int(std::lround(size.width() * scale * s))),
                                      std::max(1, int(std::lround(size.height() * scale * s)))),
                                QImage::Format_ARGB32_Premultiplied);
    return total;
}

// A layer's width once scaled to the device's height, keeping its proportions.
int layerWidth(const QSize &size, int height)
{
    return std::max(1, int(std::lround(size.width() * qreal(height) / size.height())));
}

// A light texture's size: over the canvas, 6 % larger when it sways (so its
// edges never show as it moves).
QSize lightSize(const QSize &device, const QString &motion)
{
    const qreal grow = motion == QLatin1String("sway") ? 1.06 : 1.0;
    return QSize(int(std::lround(device.width() * grow)), int(std::lround(device.height() * grow)));
}

// The span [from, to) of `extent` source pixels to scale, from the part that
// shows ([first, last]) with one pixel more either side (scaling reads a
// pixel's neighbours), widened to whole multiples of `extent / gcd(extent,
// scaled)`: there the part scales to exactly the pixels the whole image would
// (a whole number of output pixels, sampled from the same source positions),
// so preparing only what shows changes nothing that is drawn.
std::pair<int, int> spanToScale(int first, int last, int extent, int scaled)
{
    const int step = extent / std::gcd(extent, scaled);
    const int from = std::max(0, first - 1) / step * step;
    const int to = std::min(extent, (std::min(extent, last + 2) + step - 1) / step * step);
    return {from, to};
}

// The leftward offset, in canvas pixels, of a layer moving by `motion` at
// world time `t`, leaning with navigation by `parallax` times its depth.
qreal layerOffset(const QString &motion, qreal speed, qreal depth, qreal t, qreal parallax)
{
    qreal offset = parallax * depth;
    if (motion == QLatin1String("drift"))
        offset -= t * speed;
    else if (motion == QLatin1String("sway"))
        offset += std::sin(t * kTau / 14) * std::max<qreal>(16, speed);
    return offset;
}

// The device column where the repeats of a layer `width` wide start: at or
// left of 0, so that repeats every `width` cover the canvas.
int firstRepeat(qreal offset, qreal scale, int width)
{
    int x = int(std::lround(offset * scale) % width);
    if (x > 0)
        x -= width;
    return x;
}

// The rows [first, last] of `image` that show anything; an empty pair when none.
std::pair<int, int> shownRows(const QImage &image)
{
    int first = -1, last = -1;
    for (int y = 0; y < image.height(); ++y) {
        const auto *line = reinterpret_cast<const QRgb *>(image.constScanLine(y));
        bool any = false;
        for (int x = 0; x < image.width() && !any; ++x)
            any = line[x] != 0;
        if (any) {
            if (first < 0)
                first = y;
            last = y;
        }
    }
    return {first, last};
}

// The part of `image` that shows anything (premultiplied: 0 is nothing, for
// SourceOver, Screen and Plus alike).
QRect shownRect(const QImage &image)
{
    int left = image.width(), right = -1;
    const auto [first, last] = shownRows(image);
    if (first < 0)
        return {};
    for (int y = first; y <= last; ++y) {
        const auto *line = reinterpret_cast<const QRgb *>(image.constScanLine(y));
        for (int x = 0; x < left; ++x)
            if (line[x]) {
                left = x;
                break;
            }
        for (int x = image.width() - 1; x > right; --x)
            if (line[x]) {
                right = x;
                break;
            }
    }
    return QRect(QPoint(left, first), QPoint(right, last));
}

struct Layer {
    QImage image;   // premultiplied, at the device's height, the band of rows that show
    int top = 0;    // device row of its first row
    QRect shown;    // its part that shows anything: all that is painted
    QString motion;
    qreal speed = 0;   // canvas px/s
    qreal depth = 0.5;
    qreal opacity = 1;
};

struct Light {
    QImage image;   // premultiplied, its part of the texture over the canvas (a little larger, for sway)
    QPoint at;      // where the image's corner goes, device pixels, relative to the canvas's corner
    QRect shown;    // its part that shows anything: all that is painted
    QPainter::CompositionMode mode = QPainter::CompositionMode_Screen;
    QString motion;
    qreal opacity = 0.5;
};

struct Sprite {
    qreal u, v, speed, phase;
    int size;   // which of the three prepared sizes
};

struct Emitter {
    QImage images[3], mirrored[3];   // 0.8, 1 and 1.2 times its scale
    QString path;
    qreal speed = 40;
    qreal from = 0, to = 1;
    std::vector<Sprite> sprites;
};

} // namespace

// ------------------------------------------------------------------ painter

class WorldPainter : public QObject {
    Q_OBJECT
public:
    explicit WorldPainter(std::shared_ptr<ShapeWorld::Frames> frames) : m_frames(std::move(frames)) {}

public slots:
    void prepare(quint64 generation, const QVariantMap &world, const QString &root, const QSize &device, qreal scale,
                 qint64 budget);
    void setDetail(quint64 generation, int detail);
    void wake();

signals:
    void prepared(quint64 generation, bool ok, int detail, qint64 estimate, const QString &note);
    void frameReady();

private:
    void tick();
    void schedule();
    bool moving(const ShapeWorld::Params &p) const;
    Header header(const QVariant &name);
    qint64 frameBytes() const { return imageBytes(m_device, QImage::Format_RGB32); }
    qint64 fixedBytes() const;
    qint64 estimateFor(int detail);
    bool take(qint64 bytes, const QString &what, QString *why, bool *over);
    QImage decode(const QVariant &name, int maxSide, const QString &what, qint64 *scratch, QString *why, bool *over);
    bool load(int detail, QString *why, bool *over);
    void clearPrepared();
    void freeAbove(int detail);
    void paintStillLayer(const QVariantMap &spec, QImage source);
    void composeStill();
    void paintContent(QImage &target, qreal t, qreal parallax) const;
    void paintLayers(QPainter &painter, qreal t, qreal parallax) const;
    void paintEmitters(QPainter &painter, qreal t) const;
    void paintLights(QPainter &painter, qreal t) const;
    void paintFront(QImage &frame, const ShapeWorld::Params &p, qreal t);

    std::shared_ptr<ShapeWorld::Frames> m_frames;
    QTimer *m_timer = nullptr;
    quint64 m_generation = 0;
    QVariantMap m_world;
    QString m_root;
    QSize m_device;
    qreal m_scale = 1;   // device pixels per canvas pixel
    int m_detail = 4;
    int m_rate = 10;
    qint64 m_budget = 0;
    QHash<QString, Header> m_headers;   // this world's, read once
    qint64 m_left = 0;   // while preparing: what the level may still take

    QImage m_backdrop;   // RGB32, the device's size
    std::vector<Layer> m_layers;
    std::vector<Emitter> m_emitters;
    std::vector<Light> m_lights;
    bool m_stillComposed = false;   // m_backdrop holds the whole world, composed once

    QImage m_content;             // the world alone, for a transition or a still world
    qreal m_contentTime = -1;
    qreal m_contentParallax = 0;
    quint64 m_paintedVersion = ~0ULL;
    qreal m_time = 12;            // the world's own seconds; a moment where things are spread out
    QElapsedTimer m_clock;
    Pace m_pace;
    // The laboratory's timings (MUN_SHELL_TIMING): paint times, logged every
    // couple of seconds while frames are painted.
    std::vector<qreal> m_paints;
    QElapsedTimer m_since;
    QElapsedTimer m_tickStart;   // when the tick being painted began: the next is due a period after it
};

qint64 WorldPainter::fixedBytes() const
{
    // The two frames, the transition's content and one frame more for a copy
    // Qt may make of a frame (one made again in the other format as the world
    // turns opaque or not, or a texture that keeps a copy of its own); and
    // the export, held in the runtime directory's memory.
    return 4 * frameBytes() + m_world.value(QStringLiteral("bytes")).toLongLong();
}

Header WorldPainter::header(const QVariant &name)
{
    const QString path = packagePath(m_root, name);
    if (path.isEmpty())
        return {};
    auto it = m_headers.constFind(path);
    if (it == m_headers.constEnd())
        it = m_headers.insert(path, headerOf(path));
    return *it;
}

// A level at its peak, from the headers only (docs/shape.md, "Fits the
// display"), counted as load() takes it: the fixed part; what the level keeps
// (the backdrop at the device's size; each layer at the device's height, as
// wide as it scales to and at most that tall; each sprite in its three sizes
// and their mirrors; each light texture over the canvas); and, on top of all
// of it, the largest one image's decoding while it is prepared (scratchFor).
// Still (3) keeps the backdrop alone: the nearest layer is painted onto it
// straight from its decoded image.
qint64 WorldPainter::estimateFor(int detail)
{
    if (detail >= 4)
        return 0;
    const int height = m_device.height();
    qint64 kept = fixedBytes() + frameBytes();
    qint64 scratch = 0;
    const QVariantMap backdrop = m_world.value(QStringLiteral("backdrop")).toMap();
    if (backdrop.contains(QStringLiteral("image")))
        scratch = scratchFor(header(backdrop.value(QStringLiteral("image"))));
    const QVariantList layers = m_world.value(QStringLiteral("layers")).toList();
    for (int i = 0; i < layers.size(); ++i) {
        if (detail >= 2 && i != layers.size() - 1)
            continue;
        const Header h = header(layers[i].toMap().value(QStringLiteral("image")));
        scratch = std::max(scratch, scratchFor(h));
        if (detail <= 2 && h.size.isValid() && h.size.height() > 0)
            kept += imageBytes(QSize(layerWidth(h.size, height), height), QImage::Format_ARGB32_Premultiplied);
    }
    if (detail <= 1) {
        for (const QVariant &e : m_world.value(QStringLiteral("emitters")).toList()) {
            const QVariantMap emitter = e.toMap();
            const Header h = header(emitter.value(QStringLiteral("sprite")));
            scratch = std::max(scratch, scratchFor(h));
            if (h.size.isValid())
                kept += spriteBytes(h.size, emitter.value(QStringLiteral("scale"), 1).toDouble() * m_scale);
        }
    }
    if (detail == 0) {
        for (const QVariant &l : m_world.value(QStringLiteral("light")).toList()) {
            const QVariantMap light = l.toMap();
            scratch = std::max(scratch, scratchFor(header(light.value(QStringLiteral("texture")))));
            kept += imageBytes(lightSize(m_device, light.value(QStringLiteral("motion")).toString()),
                               QImage::Format_ARGB32_Premultiplied);
        }
    }
    return kept + scratch;
}

bool WorldPainter::take(qint64 bytes, const QString &what, QString *why, bool *over)
{
    if (bytes <= m_left) {
        m_left -= bytes;
        return true;
    }
    *over = true;
    *why = QStringLiteral("%1 would take %2 MiB more than the level has left")
               .arg(what)
               .arg((bytes - m_left + kMiB - 1) / kMiB);
    return false;
}

// Decodes one of the package's images (`what` names it in the journal) once
// its header says it may be: within `maxSide`, and its decoding within what
// the level has left, which is taken (*scratch) for the caller to give back
// once the image is used. Qt's allocation limit (set once by Shape) holds too.
QImage WorldPainter::decode(const QVariant &name, int maxSide, const QString &what, qint64 *scratch, QString *why,
                            bool *over)
{
    *scratch = 0;
    const QString path = packagePath(m_root, name);
    QFile file(path);
    if (path.isEmpty() || !file.open(QIODevice::ReadOnly)) {
        *why = QStringLiteral("%1 cannot be read").arg(what);
        return {};
    }
    QImageReader reader(&file, "png");
    const QSize size = reader.size();
    if (!size.isValid() || size.width() < 1 || size.height() < 1 || size.width() > maxSide || size.height() > maxSide) {
        *why = QStringLiteral("%1 is not a PNG within %2x%2").arg(what).arg(maxSide);
        return {};
    }
    const qint64 needs = scratchFor({size, reader.imageFormat()});
    if (!take(needs, what, why, over))
        return {};
    *scratch = needs;
    QImage image = reader.read();
    if (image.isNull())
        *why = QStringLiteral("%1 could not be decoded (%2)").arg(what, reader.errorString());
    return image;
}

void WorldPainter::clearPrepared()
{
    m_backdrop = {};
    m_layers.clear();
    m_emitters.clear();
    m_lights.clear();
    m_content = {};
    m_contentTime = -1;
    m_stillComposed = false;
    m_paintedVersion = ~0ULL;
}

void WorldPainter::prepare(quint64 generation, const QVariantMap &world, const QString &root, const QSize &device,
                           qreal scale, qint64 budget)
{
    if (!m_timer) {
        m_timer = new QTimer(this);
        m_timer->setSingleShot(true);
        m_timer->setTimerType(Qt::PreciseTimer);
        connect(m_timer, &QTimer::timeout, this, &WorldPainter::tick);
    }
    m_generation = generation;
    m_world = world;
    m_root = root;
    m_device = device;
    m_scale = device.height() / shapefront::kCanvasHeight;
    m_budget = budget;
    m_headers.clear();
    clearPrepared();
    m_rate = world.value(QStringLiteral("rate")).toInt() == 20 ? 20 : 10;
    Q_UNUSED(scale);   // the device's size says it: m_scale is per canvas pixel

    if (world.isEmpty() || !world.contains(QStringLiteral("backdrop")) || device.isEmpty()) {
        m_detail = 4;
        emit prepared(generation, true, 4, 0, QString());
        wake();
        return;
    }
    // The richest level the headers say fits. A level whose images would then
    // take more than that (a format the header did not foretell) is given up
    // before the memory is taken, and the next one is tried.
    int detail = 0;
    while (detail < 4 && estimateFor(detail) > budget)
        ++detail;
    QString why;
    bool over = false, ok = false;
    QElapsedTimer took;
    took.start();
    for (; detail < 4; ++detail) {
        clearPrepared();
        over = false;
        ok = load(detail, &why, &over);
        if (ok || !over)
            break;
    }
    if (!ok) {
        clearPrepared();
        m_detail = 4;
        emit prepared(generation, false, 4, estimateFor(std::min(detail, 3)),
                      detail >= 4 ? QStringLiteral("over the display's budget at every level") : why);
        wake();
        return;
    }
    m_detail = detail;
    emit prepared(generation, true, detail, estimateFor(detail), QStringLiteral("prepared in %1 ms").arg(took.elapsed()));
    wake();
}

// Prepares `detail`'s images within what it has left of the budget (m_left),
// one image at a time: each taken before its memory is, given back (its
// decoding) once used. Everything is scaled once to the device's pixels, and
// only what can show is made: the backdrop's part the canvas shows, a layer's
// band of rows that show (the whole width, which moves across the canvas and
// repeats), a light texture's part that shows. On failure *why says what,
// and *over whether it was only the budget.
bool WorldPainter::load(int detail, QString *why, bool *over)
{
    const QSize device = m_device;
    m_left = m_budget - fixedBytes();
    // The backdrop: an image covering the canvas, scaled keeping its
    // proportions and centred, or a gradient top to bottom.
    if (!take(frameBytes(), QStringLiteral("the backdrop"), why, over))
        return false;
    const QVariantMap backdrop = m_world.value(QStringLiteral("backdrop")).toMap();
    if (backdrop.contains(QStringLiteral("image"))) {
        qint64 scratch = 0;
        QImage source = decode(backdrop.value(QStringLiteral("image")), kLayerMaxSide, QStringLiteral("the backdrop"),
                               &scratch, why, over);
        if (source.isNull())
            return false;
        source = converted(std::move(source), QImage::Format_RGB32);
        const QSize cover = source.size().scaled(device, Qt::KeepAspectRatioByExpanding);
        if (cover == device) {
            // All of it shows: scaled whole.
            m_backdrop = source.scaled(device, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
        } else {
            // Only its middle shows: that part is drawn, scaled, straight into
            // the backdrop; the image at the cover's size is never made.
            const qreal sx = qreal(cover.width()) / source.width(), sy = qreal(cover.height()) / source.height();
            m_backdrop = QImage(device, QImage::Format_RGB32);
            m_backdrop.fill(Qt::black);
            QPainter painter(&m_backdrop);
            painter.setRenderHint(QPainter::SmoothPixmapTransform);
            painter.drawImage(QRectF(QPointF(0, 0), QSizeF(device)), source,
                              QRectF((cover.width() - device.width()) / 2 / sx, (cover.height() - device.height()) / 2 / sy,
                                     device.width() / sx, device.height() / sy));
        }
        source = QImage();
        m_left += scratch;
    } else {
        const QVariantList stops = backdrop.value(QStringLiteral("gradient")).toList();
        m_backdrop = QImage(device, QImage::Format_RGB32);
        QPainter painter(&m_backdrop);
        QLinearGradient gradient(0, 0, 0, device.height());
        for (int i = 0; i < stops.size(); ++i)
            gradient.setColorAt(stops.size() > 1 ? qreal(i) / (stops.size() - 1) : 0, QColor(stops[i].toString()));
        painter.fillRect(m_backdrop.rect(), gradient);
    }

    const QVariantList layers = m_world.value(QStringLiteral("layers")).toList();
    if (detail == 3) {
        // Still: the nearest layer painted onto the backdrop once, from its
        // decoded image, as it stands now; no layer is kept.
        if (!layers.isEmpty()) {
            const int i = int(layers.size()) - 1;
            qint64 scratch = 0;
            QImage source = decode(layers[i].toMap().value(QStringLiteral("image")), kLayerMaxSide,
                                   QStringLiteral("layer %1").arg(i + 1), &scratch, why, over);
            if (source.isNull())
                return false;
            paintStillLayer(layers[i].toMap(), converted(std::move(source), QImage::Format_ARGB32_Premultiplied));
            m_left += scratch;
        }
        m_stillComposed = true;
        return true;
    }
    for (int i = 0; i < layers.size(); ++i) {
        if (detail == 2 && i != layers.size() - 1)
            continue;
        const QVariantMap spec = layers[i].toMap();
        const QString what = QStringLiteral("layer %1").arg(i + 1);
        qint64 scratch = 0;
        QImage source = decode(spec.value(QStringLiteral("image")), kLayerMaxSide, what, &scratch, why, over);
        if (source.isNull())
            return false;
        source = converted(std::move(source), QImage::Format_ARGB32_Premultiplied);
        Layer layer;
        const auto [first, last] = shownRows(source);
        if (first >= 0) {
            // Scaled to the device's height, keeping its proportions.
            const int height = device.height();
            const int width = layerWidth(source.size(), height);
            const auto [top, bottom] = spanToScale(first, last, source.height(), height);
            const int rows = (bottom - top) * height / source.height();
            if (!take(imageBytes(QSize(width, rows), QImage::Format_ARGB32_Premultiplied), what, why, over))
                return false;
            if (width == source.width() && rows == bottom - top) {
                layer.image = top == 0 && bottom == source.height() ? source : source.copy(0, top, width, rows);
            } else {
                // A view of the band, not a copy, scaled.
                const QImage band(source.constScanLine(top), source.width(), bottom - top, source.bytesPerLine(),
                                  source.format());
                layer.image = band.scaled(width, rows, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
            }
            layer.top = top * height / source.height();
            const auto [shownFirst, shownLast] = shownRows(layer.image);
            if (shownFirst >= 0)
                layer.shown = QRect(0, shownFirst, layer.image.width(), shownLast - shownFirst + 1);
        }
        source = QImage();
        m_left += scratch;
        layer.motion = spec.value(QStringLiteral("motion"), QStringLiteral("still")).toString();
        layer.speed = spec.value(QStringLiteral("speed"), 0).toDouble();
        layer.depth = spec.value(QStringLiteral("depth"), 0.5).toDouble();
        layer.opacity = spec.value(QStringLiteral("opacity"), 1).toDouble();
        m_layers.push_back(std::move(layer));
    }
    if (detail >= 2)
        return true;

    const QVariantList emitters = m_world.value(QStringLiteral("emitters")).toList();
    for (int i = 0; i < emitters.size(); ++i) {
        const QVariantMap spec = emitters[i].toMap();
        const QString what = QStringLiteral("emitter %1's sprite").arg(i + 1);
        qint64 scratch = 0;
        QImage decoded = decode(spec.value(QStringLiteral("sprite")), kSpriteMaxSide, what, &scratch, why, over);
        if (decoded.isNull())
            return false;
        const QImage source = converted(std::move(decoded), QImage::Format_ARGB32_Premultiplied);
        const qreal scale = spec.value(QStringLiteral("scale"), 1).toDouble() * m_scale;
        if (!take(spriteBytes(source.size(), scale), what, why, over))
            return false;
        Emitter emitter;
        const qreal sizes[3] = {0.8, 1.0, 1.2};
        for (int s = 0; s < 3; ++s) {
            const QSize size(std::max(1, int(std::lround(source.width() * scale * sizes[s]))),
                             std::max(1, int(std::lround(source.height() * scale * sizes[s]))));
            emitter.images[s] = source.scaled(size, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
            emitter.mirrored[s] = emitter.images[s].mirrored(true, false);
        }
        m_left += scratch;
        emitter.path = spec.value(QStringLiteral("path")).toString();
        emitter.speed = spec.value(QStringLiteral("speed"), 40).toDouble();
        const QVariantList band = spec.value(QStringLiteral("band")).toList();
        if (band.size() == 2) {
            emitter.from = std::clamp(band[0].toDouble(), 0.0, 1.0);
            emitter.to = std::clamp(band[1].toDouble(), emitter.from, 1.0);
        }
        // The same sprites every time for the same package: seeded by position.
        std::mt19937 random(1000 + i);
        std::uniform_real_distribution<qreal> unit(0, 1);
        const int count = std::clamp(spec.value(QStringLiteral("count")).toInt(), 1, 128);
        for (int k = 0; k < count; ++k)
            emitter.sprites.push_back({unit(random), unit(random), 0.8 + 0.4 * unit(random), kTau * unit(random),
                                       int(unit(random) * 3) % 3});
        m_emitters.push_back(std::move(emitter));
    }
    if (detail >= 1)
        return true;

    const QVariantList lights = m_world.value(QStringLiteral("light")).toList();
    for (int i = 0; i < lights.size(); ++i) {
        const QVariantMap spec = lights[i].toMap();
        const QString what = QStringLiteral("light texture %1").arg(i + 1);
        qint64 scratch = 0;
        QImage source = decode(spec.value(QStringLiteral("texture")), kLayerMaxSide, what, &scratch, why, over);
        if (source.isNull())
            return false;
        source = converted(std::move(source), QImage::Format_ARGB32_Premultiplied);
        Light light;
        light.motion = spec.value(QStringLiteral("motion"), QStringLiteral("still")).toString();
        // Stretched over the canvas (a sway a little larger, so its edges
        // never show as it moves).
        const QSize size = lightSize(device, light.motion);
        const QRect content = shownRect(source);
        if (!content.isEmpty()) {
            const auto [left, right] = spanToScale(content.left(), content.right(), source.width(), size.width());
            const auto [top, bottom] = spanToScale(content.top(), content.bottom(), source.height(), size.height());
            const QSize part((right - left) * size.width() / source.width(), (bottom - top) * size.height() / source.height());
            if (!take(imageBytes(part, QImage::Format_ARGB32_Premultiplied), what, why, over))
                return false;
            if (part == QSize(right - left, bottom - top)) {
                light.image = part == source.size() ? source : source.copy(left, top, part.width(), part.height());
            } else {
                // A view of that part, not a copy, scaled.
                const QImage view(source.constScanLine(top) + qsizetype(left) * 4, right - left, bottom - top,
                                  source.bytesPerLine(), source.format());
                light.image = view.scaled(part, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
            }
            light.shown = shownRect(light.image);
            light.at = QPoint(left * size.width() / source.width(), top * size.height() / source.height())
                       - QPoint((size.width() - device.width()) / 2, (size.height() - device.height()) / 2);
        }
        source = QImage();
        m_left += scratch;
        light.mode = spec.value(QStringLiteral("blend")).toString() == QLatin1String("add") ? QPainter::CompositionMode_Plus
                                                                                            : QPainter::CompositionMode_Screen;
        light.opacity = spec.value(QStringLiteral("opacity"), 0.5).toDouble();
        m_lights.push_back(std::move(light));
    }
    return true;
}

void WorldPainter::freeAbove(int detail)
{
    if (detail >= 1)
        m_lights.clear();
    if (detail >= 2) {
        m_emitters.clear();
        if (m_layers.size() > 1)
            m_layers.erase(m_layers.begin(), m_layers.end() - 1);
    }
}

// Still, from the start: a layer painted once onto the backdrop as it stands
// at the world's time, straight from its decoded image; each repeat across the
// canvas draws only the part of the image that falls on the canvas.
void WorldPainter::paintStillLayer(const QVariantMap &spec, QImage source)
{
    const int width = layerWidth(source.size(), m_device.height());
    const qreal offset = layerOffset(spec.value(QStringLiteral("motion"), QStringLiteral("still")).toString(),
                                     spec.value(QStringLiteral("speed"), 0).toDouble(),
                                     spec.value(QStringLiteral("depth"), 0.5).toDouble(), m_time, 0);
    const qreal toSource = qreal(source.width()) / width;
    QPainter painter(&m_backdrop);
    painter.setRenderHint(QPainter::SmoothPixmapTransform);
    painter.setOpacity(std::clamp(spec.value(QStringLiteral("opacity"), 1).toDouble(), 0.0, 1.0));
    for (int x = firstRepeat(offset, m_scale, width); x < m_device.width(); x += width) {
        const int left = std::max(0, x), right = std::min(m_device.width(), x + width);
        painter.drawImage(QRectF(left, 0, right - left, m_device.height()), source,
                          QRectF((left - x) * toSource, 0, (right - left) * toSource, source.height()));
    }
}

// Still, stepped down to: what is left of the world (the backdrop and the
// nearest layer) composed once onto the backdrop itself, then the layer freed.
// Nothing shares the backdrop (a frame is filled from it by copying, never
// shares it), so painting on it makes no copy of it; the journal says so.
void WorldPainter::composeStill()
{
    const uchar *before = m_backdrop.constBits();
    {
        QPainter painter(&m_backdrop);
        paintLayers(painter, m_time, 0);
        paintEmitters(painter, m_time);
        paintLights(painter, m_time);
    }
    qInfo("mun-shell: shape: still world composed %s", m_backdrop.constBits() == before ? "in place" : "on a copy of the backdrop");
    m_layers.clear();
    m_emitters.clear();
    m_lights.clear();
    m_stillComposed = true;
}

void WorldPainter::setDetail(quint64 generation, int detail)
{
    if (generation != m_generation || detail <= m_detail)
        return;
    if (detail >= 4) {
        m_backdrop = {};
        freeAbove(3);
        m_detail = 4;
    } else {
        freeAbove(detail);
        m_detail = detail;
        if (detail == 3 && !m_stillComposed)
            composeStill();
    }
    m_content = {};
    m_contentTime = -1;
    m_paintedVersion = ~0ULL;
    emit prepared(generation, true, m_detail, 0, QStringLiteral("stepped down"));
    wake();
}

bool WorldPainter::moving(const ShapeWorld::Params &p) const
{
    if (m_detail >= 3 || m_backdrop.isNull() || p.still)
        return false;
    if (!p.running && m_pace.still())
        return false;
    for (const Layer &layer : m_layers)
        if (layer.motion == QLatin1String("drift") || layer.motion == QLatin1String("sway"))
            return true;
    for (const Light &light : m_lights)
        if (light.motion != QLatin1String("still"))
            return true;
    return !m_emitters.empty();
}

void WorldPainter::wake()
{
    if (m_timer && !m_timer->isActive())
        m_timer->start(0);
}

void WorldPainter::schedule()
{
    ShapeWorld::Params p;
    {
        QMutexLocker lock(&m_frames->mutex);
        p = m_frames->params;
    }
    const bool transitioning = p.progress > 0 && p.progress < 1;
    int rate = 0;
    if (moving(p))
        rate = m_detail == 2 ? 10 : m_rate;
    if (transitioning)
        rate = std::max(rate, ShapeWorld::kTransitionRate);
    if (rate > 0) {
        // A period from when this tick began, not from when its painting ended.
        const qint64 spent = m_tickStart.isValid() ? m_tickStart.elapsed() : 0;
        m_timer->start(int(std::max<qint64>(1, 1000 / rate - spent)));
    } else {
        m_clock.invalidate();
    }
}

void WorldPainter::tick()
{
    m_tickStart.start();
    ShapeWorld::Params p;
    int target;
    {
        QMutexLocker lock(&m_frames->mutex);
        p = m_frames->params;
        target = m_frames->shown == 0 ? 1 : 0;
        if (m_frames->ready == target) {
            // The last frame is not on screen yet: this one is skipped, never
            // waited for. The GUI wakes the painter when it takes that frame;
            // a moving world's next tick is due anyway, a still one's not.
            m_frames->wanted = true;
            lock.unlock();
            schedule();
            return;
        }
    }

    // The world's own time, eased to a stop at rest.
    m_pace.setMoving(p.running && !p.still);
    const qreal dt = m_clock.isValid() ? std::min<qreal>(0.1, m_clock.restart() / 1000.0) : 0;
    if (!m_clock.isValid())
        m_clock.start();
    const bool motion = moving(p);
    if (motion)
        m_time += m_pace.advance(dt);
    if (!motion && p.version == m_paintedVersion) {
        schedule();
        return;   // nothing new to show
    }

    QElapsedTimer painting;
    painting.start();
    // The frame the scene graph does not hold: written in place (its only
    // other reference, the texture of an older frame, is gone). Opaque once
    // the world covers the canvas.
    const bool world = !m_backdrop.isNull();
    const QImage::Format format = world && p.progress >= 1 ? QImage::Format_RGB32 : QImage::Format_ARGB32_Premultiplied;
    {
        QMutexLocker lock(&m_frames->mutex);
        if (m_frames->images[target].size() != m_device || m_frames->images[target].format() != format)
            m_frames->images[target] = QImage(m_device, format);
    }
    QImage &out = m_frames->images[target];
    if (world && p.progress >= 1 && p.dim <= 0 && motion) {
        paintContent(out, m_time, p.parallax);   // the steady state: straight into the frame
    } else {
        if (world && (m_content.size() != m_device || m_contentTime != m_time || m_contentParallax != p.parallax)) {
            if (m_content.size() != m_device)
                m_content = QImage(m_device, QImage::Format_RGB32);
            paintContent(m_content, m_time, p.parallax);
            m_contentTime = m_time;
            m_contentParallax = p.parallax;
        }
        paintFront(out, p, m_time);
    }

    m_paintedVersion = p.version;
    {
        QMutexLocker lock(&m_frames->mutex);
        m_frames->ready = target;
    }
    emit frameReady();
    static const bool timing = qEnvironmentVariableIsSet("MUN_SHELL_TIMING");
    if (timing) {
        m_paints.push_back(painting.nsecsElapsed() / 1e6);
        if (!m_since.isValid())
            m_since.start();
        if (m_since.elapsed() >= 2000) {
            std::vector<qreal> sorted = m_paints;
            std::sort(sorted.begin(), sorted.end());
            qInfo("mun-shell: world paint %zu frames in %lld ms, p50 %.1f ms, p95 %.1f ms (detail %d, %s)", sorted.size(),
                  qlonglong(m_since.elapsed()), sorted[sorted.size() / 2], sorted[size_t(0.95 * (sorted.size() - 1))], m_detail,
                  p.progress >= 1 ? "whole" : "in transition");
            m_paints.clear();
            m_since.restart();
        }
    }
    schedule();
}

void WorldPainter::paintContent(QImage &target, qreal t, qreal parallax) const
{
    if (m_backdrop.size() == target.size() && m_backdrop.format() == target.format())
        std::memcpy(target.bits(), m_backdrop.constBits(), size_t(m_backdrop.sizeInBytes()));
    QPainter painter(&target);
    if (m_backdrop.size() != target.size() || m_backdrop.format() != target.format())
        painter.drawImage(0, 0, m_backdrop);
    paintLayers(painter, t, parallax);
    paintEmitters(painter, t);
    paintLights(painter, t);
}

void WorldPainter::paintLayers(QPainter &painter, qreal t, qreal parallax) const
{
    const int width = m_device.width();
    for (const Layer &layer : m_layers) {
        if (layer.image.isNull() || layer.shown.isEmpty())
            continue;
        // Canvas pixels: a drift moves steadily, a sway comes and goes, every
        // layer follows navigation by its depth.
        const qreal offset = layerOffset(layer.motion, layer.speed, layer.depth, t, parallax);
        const int w = layer.image.width();
        painter.setOpacity(std::clamp(layer.opacity, 0.0, 1.0));
        for (int x = firstRepeat(offset, m_scale, w); x < width; x += w)
            painter.drawImage(QPoint(x, layer.top + layer.shown.top()), layer.image, layer.shown);
    }
    painter.setOpacity(1);
}

void WorldPainter::paintEmitters(QPainter &painter, qreal t) const
{
    const qreal W = shapefront::kCanvasWidth, H = shapefront::kCanvasHeight;
    const QPointF orb(470, 570);
    for (const Emitter &emitter : m_emitters) {
        const qreal top = emitter.from * H, band = std::max<qreal>(1, (emitter.to - emitter.from) * H);
        for (size_t i = 0; i < emitter.sprites.size(); ++i) {
            const Sprite &s = emitter.sprites[i];
            const qreal speed = emitter.speed * s.speed;
            qreal x = 0, y = 0, alpha = 1;
            bool left = false;
            if (emitter.path == QLatin1String("rise") || emitter.path == QLatin1String("fall")) {
                const qreal along = std::fmod(s.v + t * speed / band, 1.0);
                const bool rise = emitter.path == QLatin1String("rise");
                y = rise ? top + band * (1 - along) : top + band * along;
                x = s.u * (W + 40) - 20 + std::sin(t * (rise ? 0.8 : 0.5) + s.phase) * (rise ? 14 : 22);
                alpha = std::min<qreal>(1, std::min(along, 1 - along) / 0.1);
            } else if (emitter.path == QLatin1String("drift")) {
                x = std::fmod(s.u * (W + 200) + t * speed, W + 200) - 100;
                y = top + s.v * band + std::sin(t * 0.4 + s.phase) * 12;
            } else if (emitter.path == QLatin1String("school")) {
                // One group crossing and coming back, every sprite in its place
                // within it, the whole group rising and falling slowly.
                const qreal length = W + 900;
                const qreal travelled = t * emitter.speed;
                const int pass = int(travelled / length);
                const qreal along = std::fmod(travelled, length);
                left = pass % 2 == 1;
                const qreal centreX = left ? W + 450 - along : along - 450;
                const qreal centreY = top + band * (0.5 + 0.3 * std::sin(pass * 1.7 + 0.5)) + std::sin(t * 0.35) * band * 0.1;
                x = centreX + (s.u - 0.5) * 360 * s.speed + std::sin(t + s.phase) * 10;
                y = centreY + (s.v - 0.5) * std::min<qreal>(120, band) + std::sin(t * 1.3 + s.phase) * 8;
            } else {   // orbit
                const qreal radius = 280 + s.v * std::max<qreal>(80, band * 0.6);
                const qreal angle = s.phase + t * speed / radius;
                x = orb.x() + std::cos(angle) * radius;
                y = orb.y() + std::sin(angle) * radius * 0.32;
                left = std::sin(angle) > 0;
            }
            const QImage &image = left ? emitter.mirrored[s.size] : emitter.images[s.size];
            painter.setOpacity(std::clamp<qreal>(alpha, 0, 1));
            painter.drawImage(QPointF(x * m_scale - image.width() / 2.0, y * m_scale - image.height() / 2.0), image);
        }
    }
    painter.setOpacity(1);
}

void WorldPainter::paintLights(QPainter &painter, qreal t) const
{
    for (const Light &light : m_lights) {
        if (light.image.isNull() || light.shown.isEmpty())
            continue;
        const QRect &shown = light.shown;
        qreal opacity = std::clamp(light.opacity, 0.0, 1.0);
        QPoint at = light.at;
        painter.setCompositionMode(light.mode);
        if (light.motion == QLatin1String("sway")) {
            at += QPoint(int(std::lround(std::sin(t * kTau / 16) * 28 * m_scale)), 0);
        } else if (light.motion == QLatin1String("pulse")) {
            opacity *= 0.72 + 0.28 * (0.5 + 0.5 * std::sin(t * kTau / 5));
        }
        painter.setOpacity(opacity);
        if (light.motion == QLatin1String("ripple")) {
            // Bands of rows, each shifted by two slow waves: light through water.
            const int step = std::max(4, int(std::lround(8 * m_scale)));
            for (int y = shown.top(); y <= shown.bottom(); y += step) {
                const qreal row = (light.at.y() + y) / m_scale;
                const qreal dx = (std::sin(row * 0.018 + t * 1.2) * 5 + std::sin(row * 0.041 - t * 0.8) * 3) * m_scale;
                painter.drawImage(QPointF(at.x() + shown.left() + dx, at.y() + y), light.image,
                                  QRectF(shown.left(), y, shown.width(), std::min(step, shown.bottom() + 1 - y)));
            }
        } else {
            painter.drawImage(at + shown.topLeft(), light.image, shown);
        }
    }
    painter.setOpacity(1);
    painter.setCompositionMode(QPainter::CompositionMode_SourceOver);
}

void WorldPainter::paintFront(QImage &frame, const ShapeWorld::Params &p, qreal t)
{
    const bool world = !m_content.isNull() && !m_backdrop.isNull();
    const bool whole = p.progress >= 1;
    if (!whole || !world)
        frame.fill(Qt::transparent);
    QPainter painter(&frame);
    painter.setRenderHint(QPainter::Antialiasing);
    const QPointF orb = p.orb * m_scale;
    const qreal s = m_scale;

    // Where the world is: inside the front.
    QRegion inside;
    QPainterPath edge;
    if (whole) {
        inside = QRegion(frame.rect());
    } else if (p.kind == QLatin1String("tide")) {
        const qreal radius = shapefront::tideRadius(p.orb, p.progress) * s;
        if (radius > 0.5) {
            QPolygonF polygon;
            constexpr int kPoints = 180;
            for (int i = 0; i < kPoints; ++i) {
                const qreal th = kTau * i / kPoints;
                const qreal r = radius + (6 + 4 * std::sin(t * 0.9)) * s * std::sin(th * 7 + t * 1.6);
                polygon << orb + QPointF(std::cos(th) * r, std::sin(th) * r * 0.96);
            }
            inside = QRegion(polygon.toPolygon());
            edge.addPolygon(polygon);
            edge.closeSubpath();
        }
    } else if (p.kind == QLatin1String("sweep")) {
        const qreal x = shapefront::sweepX(p.progress) * s;
        if (x > 0)
            inside = QRegion(0, 0, int(std::ceil(x)), frame.height());
        edge.moveTo(x, 0);
        edge.lineTo(x, frame.height());
    } else {
        inside = QRegion(frame.rect());
    }

    if (world && !inside.isEmpty()) {
        painter.setClipRegion(inside);
        if (p.kind == QLatin1String("fade") && !whole)
            painter.setOpacity(shapefront::smooth(p.progress));
        painter.drawImage(0, 0, m_content);
        if (p.dim > 0) {
            QColor scrim = p.scrim;
            scrim.setAlphaF(std::clamp<qreal>(p.dim, 0, 1));
            painter.fillRect(frame.rect(), scrim);
        }
        painter.setOpacity(1);
        painter.setClipping(false);
    }

    // The front itself, while it moves: MUN's line of light, a haze outside
    // it and the game's accent inside.
    if (!whole && !edge.isEmpty()) {
        const auto pen = [&](qreal width, const QColor &colour, qreal alpha) {
            QColor c = colour;
            c.setAlphaF(alpha);
            return QPen(c, width * s, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin);
        };
        painter.setBrush(Qt::NoBrush);
        painter.setPen(pen(18, p.frontLight, 0.07));
        painter.drawPath(edge);
        painter.setPen(pen(8, p.frontLight, 0.16));
        painter.drawPath(edge);
        painter.setPen(pen(2.6, p.frontLight, 0.85));
        painter.drawPath(edge);
        if (p.kind == QLatin1String("tide")) {
            const qreal radius = shapefront::tideRadius(p.orb, p.progress) * s;
            if (radius > 30 * s) {
                painter.setPen(pen(1.5, p.frontAccent, 0.4));
                const qreal inner = radius - 26 * s;
                painter.drawEllipse(orb, inner, inner * 0.96);
            }
        }
    }
}

// ------------------------------------------------------------------ item

ShapeWorld::ShapeWorld(QQuickItem *parent) : QQuickItem(parent), m_frames(std::make_shared<Frames>())
{
    setFlag(ItemHasContents, true);
    m_painter = new WorldPainter(m_frames);
    m_painter->moveToThread(&m_thread);
    connect(&m_thread, &QThread::finished, m_painter, &QObject::deleteLater);
    connect(this, &ShapeWorld::prepareRequested, m_painter, &WorldPainter::prepare, Qt::QueuedConnection);
    connect(this, &ShapeWorld::detailRequested, m_painter, &WorldPainter::setDetail, Qt::QueuedConnection);
    connect(this, &ShapeWorld::wakeRequested, m_painter, &WorldPainter::wake, Qt::QueuedConnection);
    connect(m_painter, &WorldPainter::prepared, this, &ShapeWorld::onPrepared, Qt::QueuedConnection);
    connect(m_painter, &WorldPainter::frameReady, this, &ShapeWorld::onFrame, Qt::QueuedConnection);
    m_thread.setObjectName(QStringLiteral("shape-world"));
    m_thread.start(QThread::LowPriority);
}

ShapeWorld::~ShapeWorld()
{
    m_thread.quit();
    m_thread.wait();
}

void ShapeWorld::setWorld(const QVariantMap &world)
{
    if (world == m_world)
        return;
    m_world = world;
    emit worldChanged();
    prepare();
}

void ShapeWorld::setRoot(const QString &root)
{
    if (root == m_root)
        return;
    m_root = root;
    emit worldChanged();
    prepare();
}

void ShapeWorld::prepare()
{
    if (!window() || width() <= 0 || height() <= 0)
        return;
    const qreal scale = window()->effectiveDevicePixelRatio();
    m_device = QSize(int(std::lround(width() * scale)), int(std::lround(height() * scale)));
    m_budget = budgetFor(m_device);
    ++m_generation;
    m_ready = m_world.isEmpty();
    m_failed = false;
    m_detail = 4;
    m_estimate = 0;
    emit preparedChanged();
    emit prepareRequested(m_generation, m_world, m_root, m_device, scale, m_budget);
}

void ShapeWorld::onPrepared(quint64 generation, bool ok, int detail, qint64 estimate, const QString &note)
{
    if (generation != m_generation)
        return;   // a newer world came since
    const bool first = !m_ready;
    m_ready = true;
    m_failed = !ok;
    m_detail = detail;
    if (estimate > 0)
        m_estimate = estimate;
    if (!m_world.isEmpty()) {
        if (!ok)
            qWarning("mun-shell: shape: the world %s; the palette alone is used", qPrintable(note));
        else
            qInfo("mun-shell: shape: world at detail %d (%s; estimate %lld MiB of %lld at %dx%d)", detail,
                  qPrintable(note), qlonglong(m_estimate / kMiB), qlonglong(m_budget / kMiB), m_device.width(),
                  m_device.height());
    }
    Q_UNUSED(first);
    emit preparedChanged();
}

void ShapeWorld::stepDown(const QString &why)
{
    if (!drawn())
        return;
    qInfo("mun-shell: shape: world stepped down from detail %d (%s)", m_detail, qPrintable(why));
    emit detailRequested(m_generation, m_detail + 1);
}

void ShapeWorld::onFrame()
{
    update();
}

QSGNode *ShapeWorld::updatePaintNode(QSGNode *old, UpdatePaintNodeData *)
{
    auto *node = static_cast<QSGImageNode *>(old);
    if (width() <= 0 || height() <= 0 || !window()) {
        delete node;
        return nullptr;
    }
    QImage image;
    int index = -1;
    bool wanted = false;
    {
        QMutexLocker lock(&m_frames->mutex);
        if (m_frames->ready >= 0) {
            index = m_frames->ready;
            image = m_frames->images[index];
            m_frames->ready = -1;
            wanted = std::exchange(m_frames->wanted, false);
        }
    }
    if (wanted)
        emit wakeRequested();   // the tick that found this frame waiting
    if (index >= 0 && !image.isNull()) {
        if (!node) {
            node = window()->createImageNode();
            node->setOwnsTexture(true);
        }
        node->setTexture(window()->createTextureFromImage(image));
        image = QImage();
        {
            QMutexLocker lock(&m_frames->mutex);
            m_frames->shown = index;
        }
        ++m_framesShown;
        QMetaObject::invokeMethod(this, &ShapeWorld::framesChanged, Qt::QueuedConnection);
    }
    if (!node)
        return nullptr;
    node->setRect(boundingRect());
    node->setSourceRect(QRectF(QPointF(0, 0), node->texture()->textureSize()));
    return node;
}

void ShapeWorld::geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry)
{
    QQuickItem::geometryChange(newGeometry, oldGeometry);
    if (newGeometry.size() != oldGeometry.size())
        prepare();
}

void ShapeWorld::itemChange(ItemChange change, const ItemChangeData &value)
{
    QQuickItem::itemChange(change, value);
    if (change == ItemSceneChange || change == ItemDevicePixelRatioHasChanged)
        prepare();
}

void ShapeWorld::pushParams()
{
    {
        QMutexLocker lock(&m_frames->mutex);
        Params &p = m_frames->params;
        p.kind = m_transition;
        p.progress = m_progress;
        p.orb = m_orb;
        p.dim = m_dim;
        p.scrim = m_scrim;
        p.parallax = m_parallax;
        p.running = m_running;
        p.still = m_still;
        p.frontLight = m_frontLight;
        p.frontAccent = m_frontAccent;
        ++p.version;
    }
    emit wakeRequested();
}

void ShapeWorld::setTransition(const QString &kind)
{
    const QString valid = kind == QLatin1String("tide") || kind == QLatin1String("sweep") ? kind : QStringLiteral("fade");
    if (valid == m_transition)
        return;
    m_transition = valid;
    emit transitionChanged();
    pushParams();
}

void ShapeWorld::setProgress(qreal progress)
{
    progress = std::clamp<qreal>(std::isfinite(progress) ? progress : 0, 0, 1);
    if (qFuzzyCompare(progress + 1, m_progress + 1))
        return;
    const bool covered = covering();
    m_progress = progress;
    emit progressChanged();
    if (covered != covering())
        emit preparedChanged();
    pushParams();
}

void ShapeWorld::setOrb(const QPointF &orb)
{
    if (orb == m_orb)
        return;
    m_orb = orb;
    emit orbChanged();
    pushParams();
}

void ShapeWorld::setDim(qreal dim)
{
    dim = std::clamp<qreal>(dim, 0, 1);
    if (qFuzzyCompare(dim + 1, m_dim + 1))
        return;
    m_dim = dim;
    emit dimChanged();
    pushParams();
}

void ShapeWorld::setScrimColour(const QColor &colour)
{
    if (colour == m_scrim)
        return;
    m_scrim = colour;
    emit dimChanged();
    pushParams();
}

void ShapeWorld::setParallax(qreal parallax)
{
    if (qFuzzyCompare(parallax + 1, m_parallax + 1))
        return;
    m_parallax = parallax;
    emit parallaxChanged();
    pushParams();
}

void ShapeWorld::setRunning(bool running)
{
    if (running == m_running)
        return;
    m_running = running;
    emit runningChanged();
    pushParams();
}

void ShapeWorld::setStill(bool still)
{
    if (still == m_still)
        return;
    m_still = still;
    emit stillChanged();
    pushParams();
}

void ShapeWorld::setFrontLight(const QColor &colour)
{
    if (colour == m_frontLight)
        return;
    m_frontLight = colour;
    emit frontChanged();
    pushParams();
}

void ShapeWorld::setFrontAccent(const QColor &colour)
{
    if (colour == m_frontAccent)
        return;
    m_frontAccent = colour;
    emit frontChanged();
    pushParams();
}

#include "shapeworld.moc"
