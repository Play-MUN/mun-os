#include "motionscene.h"

#include "pngchunks.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QImageReader>
#include <QPainter>
#include <QPointer>
#include <QRegularExpression>
#include <QQuickWindow>
#include <QRadialGradient>
#include <QSGGeometryNode>
#include <QSGImageNode>
#include <QSGMaterial>
#include <QSGMaterialShader>
#include <QSGOpacityNode>
#include <QSGRendererInterface>
#include <QSGTexture>
#include <QSGTransformNode>
#include <QMatrix4x4>

#ifdef __linux__
#include <pthread.h>
#endif

#include <algorithm>
#include <functional>
#include <utility>
#include <chrono>
#include <cmath>
#include <cstring>

using namespace motion;

namespace {

constexpr qreal kCanvasWidth = 1920;
constexpr qreal kCanvasHeight = 1080;
constexpr qreal kTau = 6.283185307179586;
constexpr qreal kHomeSpeed = 120;      // canvas pixels a second, going back to rest
// The motion's images decoded, together: the checker's budget
// (mun_card.motion.MOTION_MAX_DECODED), held here as well.
constexpr qint64 kMotionDecodedMaxBytes = 64 * 1024 * 1024;
// The largest side of any image a motion draws (a sheet, a plane, a light):
// the checker's (mun_card.shape.SIDE_LIMITS).
constexpr int kMotionMaxSide = 2048;
// The things one frame lists, at most: a package the checker accepts lists
// some 4,300 at the very most (160 figures in 24 bands, 320 particles,
// planes and lights repeated across the screen); past this, the nearest
// are not drawn. And the quads one node of the graph way draws, within its
// 16-bit indices (4 vertices a quad).
constexpr size_t kMaxDrawItems = 8192;
constexpr size_t kMaxRunQuads = 65536 / 4 - 1;

// The marker that names the insertion whose motion is being decoded (Shape
// reads it at start): in the runtime directory, which outlives the shell's
// restarts; none without one.
QString decodingMarker()
{
    const QString dir = qEnvironmentVariable("XDG_RUNTIME_DIR");
    return dir.isEmpty() ? QString() : dir + QStringLiteral("/shape-decoding-motion");
}

// Writes the marker, whole, before anything is decoded. False, with the
// reason, if it is not there afterwards: the motion is then not decoded.
bool writeDecodingMarker(const QString &path, const QString &insertion, QString *why)
{
    if (path.isEmpty()) {
        *why = QStringLiteral("there is no runtime directory");
        return false;
    }
    QFile file(path);
    const QByteArray bytes = insertion.toLatin1();
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate) || file.write(bytes) != bytes.size() || !file.flush()) {
        *why = QStringLiteral("%1: %2").arg(path, file.errorString());
        file.close();
        QFile::remove(path);
        return false;
    }
    file.close();
    if (file.error() != QFileDevice::NoError) {
        *why = QStringLiteral("%1: %2").arg(path, file.errorString());
        QFile::remove(path);
        return false;
    }
    return true;
}

// The card service publishes a package's export as <insertion>.<attempt>
// under its root (services/mun-cardd/cardd.py, ShapeExport): the insertion
// sixteen lowercase hexadecimal digits (secrets.token_hex(8)), the attempt
// a number from 1. A console package's motion is loaded from its own
// insertion's export only.
const QRegularExpression kExportName(QStringLiteral("^([0-9a-f]{16})\\.([1-9][0-9]{0,8})$"));

qreal number(const QVariant &value, qreal otherwise = 0)
{
    bool ok = false;
    const qreal v = value.toDouble(&ok);
    return ok ? v : otherwise;
}

QPointF pointOf(const QVariant &value)
{
    const QVariantList list = value.toList();
    return list.size() >= 2 ? QPointF(number(list.at(0)), number(list.at(1))) : QPointF();
}

// A path's points: [x, y], at depth `z`, or [x, y, z].
std::vector<QVector3D> pointsOf(const QVariant &value, qreal z)
{
    std::vector<QVector3D> points;
    for (const QVariant &p : value.toList()) {
        const QVariantList list = p.toList();
        points.push_back(QVector3D(float(number(list.value(0))), float(number(list.value(1))),
                                   float(list.size() >= 3 ? number(list.at(2)) : z)));
    }
    return points;
}

QVector3D at3(QPointF p, qreal z)
{
    return QVector3D(float(p.x()), float(p.y()), float(z));
}

Vary varyOf(const QVariant &value)
{
    Vary vary;
    const QVariantMap map = value.toMap();
    for (auto it = map.cbegin(); it != map.cend(); ++it) {
        const QVariantMap c = it.value().toMap();
        Curve curve;
        curve.min = number(c.value("min"));
        curve.max = number(c.value("max"));
        curve.period = std::max<qreal>(0.2, number(c.value("period"), 1));
        curve.shape = c.value("shape").toString();
        curve.phase = number(c.value("phase"));
        vary.curves.insert(it.key(), curve);
    }
    return vary;
}

// A smooth curve through the points (Catmull-Rom), sampled about every 12
// canvas pixels, depth with them. `ghost`, when given, stands before the
// first point, so the curve leaves it the way the actor was already going;
// a closed curve comes back to its start.
std::vector<QVector3D> smoothed(const std::vector<QVector3D> &points, const QVector3D *ghost, bool closed,
                                std::vector<qreal> *params = nullptr)
{
    const long n = long(points.size());
    if (n < 2 || (n == 2 && !ghost && !closed)) {
        if (params) {
            params->clear();
            for (long i = 0; i < n; ++i)
                params->push_back(i);
        }
        return points;
    }
    if (params)
        params->clear();
    auto at = [&](long i) -> QVector3D {
        if (closed)
            return points[size_t(((i % n) + n) % n)];
        if (i < 0)
            return ghost ? *ghost : points.front();
        return points[size_t(std::min(i, n - 1))];
    };
    std::vector<QVector3D> out;
    const long spans = closed ? n : n - 1;
    for (long i = 0; i < spans; ++i) {
        const QVector3D p0 = at(i - 1), p1 = at(i), p2 = at(i + 1), p3 = at(i + 2);
        const int steps = std::clamp(int(std::hypot(p2.x() - p1.x(), p2.y() - p1.y()) / 12), 4, 48);
        for (int k = 0; k < steps; ++k) {
            const float u = float(k) / steps, u2 = u * u, u3 = u2 * u;
            out.push_back(0.5f * (2 * p1 + (p2 - p0) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u2
                                  + (3 * p1 - p0 - 3 * p2 + p3) * u3));
            if (params)
                params->push_back(i + qreal(k) / steps);    // which point it is near: i + how far to the next
        }
    }
    if (params)
        params->push_back(spans);
    out.push_back(closed ? points.front() : points.back());
    return out;
}

// The point at distance `d` along a polyline whose running lengths are `at`.
QVector3D alongDistance(const std::vector<QVector3D> &points, const std::vector<qreal> &at, qreal d)
{
    if (points.empty())
        return {};
    if (d <= 0 || points.size() == 1)
        return points.front();
    if (d >= at.back())
        return points.back();
    const size_t i = size_t(std::upper_bound(at.begin(), at.end(), d) - at.begin());
    const qreal span = at[i] - at[i - 1];
    return points[i - 1] + (points[i] - points[i - 1]) * float(span > 0 ? (d - at[i - 1]) / span : 0);
}

qreal smooth01(qreal x)
{
    x = std::clamp<qreal>(x, 0, 1);
    return x * x * (3 - 2 * x);
}

// How quickly a value follows its goal: the share to close in `dt` seconds
// with a time constant `tau`.
qreal follow(qreal dt, qreal tau)
{
    return 1 - std::exp(-dt / std::max<qreal>(0.001, tau));
}

qreal snap(qreal value, qreal step)
{
    return step > 1 ? std::round(value / step) * step : value;
}

// The scene graph's side of each backend: what a node keeps between frames.
class GraphRoot : public QSGNode {
public:
    ~GraphRoot() override { qDeleteAll(textures); }
    std::shared_ptr<std::vector<QImage>> source;
    QHash<int, QSGTexture *> textures;
    std::vector<QSGOpacityNode *> fades;
    std::vector<QSGTransformNode *> turns;
    std::vector<qreal> angles;       // the rotation each transform holds
    std::vector<QSGImageNode *> nodes;
};

// The graphics path's side of the graph backend: every run of things drawn
// from one image, one after the other in depth, is one geometry node of
// quads (sprite.vert): each quad placed, turned and mirrored here, its
// opacity per vertex. Some tens of nodes for hundreds of things, where one
// node each kept the board's CPU from the display's rate.
class SpriteMaterial : public QSGMaterial {
public:
    // RequiresFullMatrix: each run is drawn as its own batch, with the
    // standard shader (the renderer would otherwise merge runs and ask for
    // a batchable variant)
    SpriteMaterial()
    {
        setFlag(Blending);
        setFlag(RequiresFullMatrix);
    }
    QSGMaterialType *type() const override
    {
        static QSGMaterialType kind;
        return &kind;
    }
    QSGMaterialShader *createShader(QSGRendererInterface::RenderMode) const override;
    int compare(const QSGMaterial *other) const override
    {
        const auto *that = static_cast<const SpriteMaterial *>(other);
        return texture == that->texture ? 0 : texture < that->texture ? -1 : 1;
    }
    QSGTexture *texture = nullptr;    // the root's
};

class SpriteShader : public QSGMaterialShader {
public:
    SpriteShader()
    {
        setShaderFileName(VertexStage, QStringLiteral(":/shaders/sprite.vert.qsb"));
        setShaderFileName(FragmentStage, QStringLiteral(":/shaders/sprite.frag.qsb"));
    }
    bool updateUniformData(RenderState &state, QSGMaterial *, QSGMaterial *) override
    {
        QByteArray *buffer = state.uniformData();
        bool changed = false;
        if (state.isMatrixDirty()) {
            const QMatrix4x4 m = state.combinedMatrix();
            std::memcpy(buffer->data(), m.constData(), 64);
            changed = true;
        }
        if (state.isOpacityDirty()) {
            const float opacity = state.opacity();
            std::memcpy(buffer->data() + 64, &opacity, 4);
            changed = true;
        }
        return changed;
    }
    void updateSampledImage(RenderState &state, int binding, QSGTexture **texture, QSGMaterial *newMaterial,
                            QSGMaterial *) override
    {
        if (binding != 1)
            return;
        auto *material = static_cast<SpriteMaterial *>(newMaterial);
        if (material->texture)
            material->texture->commitTextureOperations(state.rhi(), state.resourceUpdateBatch());
        *texture = material->texture;
    }
};

QSGMaterialShader *SpriteMaterial::createShader(QSGRendererInterface::RenderMode) const
{
    return new SpriteShader;
}

struct SpriteVertex {
    float x, y, u, v, opacity;
};

const QSGGeometry::AttributeSet &spriteAttributes()
{
    static const QSGGeometry::Attribute list[] = {
        QSGGeometry::Attribute::createWithAttributeType(0, 2, QSGGeometry::FloatType, QSGGeometry::PositionAttribute),
        QSGGeometry::Attribute::createWithAttributeType(1, 2, QSGGeometry::FloatType, QSGGeometry::TexCoordAttribute),
        QSGGeometry::Attribute::createWithAttributeType(2, 1, QSGGeometry::FloatType, QSGGeometry::UnknownAttribute),
    };
    static const QSGGeometry::AttributeSet set = {3, sizeof(SpriteVertex), list};
    return set;
}

class BatchRoot : public QSGNode {
public:
    ~BatchRoot() override { qDeleteAll(textures); }
    std::shared_ptr<std::vector<QImage>> source;
    QHash<int, QSGTexture *> textures;
    std::vector<QSGGeometryNode *> runs;
};

class FrameRoot : public QSGNode {
public:
    ~FrameRoot() override { delete texture; }
    QSGImageNode *node = nullptr;
    QSGTexture *texture = nullptr;
};

// An animation with no end whose every advance is a frame of the window.
class FrameDriver : public QAbstractAnimation {
public:
    FrameDriver(std::function<void()> frame, QObject *parent) : QAbstractAnimation(parent), m_frame(std::move(frame)) {}
    int duration() const override { return -1; }

protected:
    void updateCurrentTime(int) override { m_frame(); }

private:
    std::function<void()> m_frame;
};

} // namespace

qreal Curve::at(qreal t, qreal phase) const
{
    const qreal u = std::fmod(t / period + this->phase + phase, 1.0);
    qreal w;
    if (shape == QLatin1String("saw"))
        w = u;
    else if (shape == QLatin1String("step"))
        w = u < 0.5 ? 0 : 1;
    else if (shape == QLatin1String("fade"))
        w = std::clamp<qreal>(std::min(u, 1 - u) / 0.1, 0, 1);   // in over the first tenth, out over the last
    else
        w = 0.5 - 0.5 * std::cos(u * kTau);
    return min + (max - min) * w;
}

qreal Vary::value(const QString &property, qreal t, qreal phase, qreal otherwise) const
{
    const auto it = curves.constFind(property);
    return it == curves.cend() ? otherwise : it->at(t, phase);
}

MotionScene::MotionScene(QQuickItem *parent) : QQuickItem(parent)
{
    setFlag(ItemHasContents, true);
    const QString wanted = qEnvironmentVariable("MUN_SHELL_MOTION_BACKEND");
    m_backend = wanted == QLatin1String("cpu") ? QStringLiteral("cpu") : QStringLiteral("graph");
    m_trace = !qEnvironmentVariableIsEmpty("MUN_SHELL_MOTION_TRACE");
    m_timer.setTimerType(Qt::PreciseTimer);
    connect(&m_timer, &QTimer::timeout, this, &MotionScene::tick);
    m_driver = new FrameDriver([this] { onFrame(); }, this);
    if (m_backend == QLatin1String("cpu"))
        m_composer = std::thread([this] { composerLoop(); });
}

MotionScene::~MotionScene()
{
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_stop = true;
    }
    m_wake.notify_all();
    if (m_composer.joinable())
        m_composer.join();
    if (m_loader.joinable())
        m_loader.join();
}

QVariantMap MotionScene::source() const
{
    return {{QStringLiteral("motion"), m_motion}, {QStringLiteral("root"), m_root},
            {QStringLiteral("insertion"), m_insertion}};
}

void MotionScene::setSource(const QVariantMap &source)
{
    // All three at once: a load never starts from one package's motion and
    // another's export or insertion.
    const QVariantMap motion = source.value(QStringLiteral("motion")).toMap();
    const QString root = source.value(QStringLiteral("root")).toString();
    const QString insertion = source.value(QStringLiteral("insertion")).toString();
    if (motion == m_motion && root == m_root && insertion == m_insertion)
        return;
    m_motion = motion;
    m_root = root;
    m_insertion = insertion;
    emit motionChanged();
    reload();
}

void MotionScene::setDisc(QPointF disc)
{
    if (disc == m_disc)
        return;
    m_disc = disc;
    emit discChanged();
}

void MotionScene::setRunning(bool running)
{
    if (running == m_running)
        return;
    m_running = running;
    emit runningChanged();
    if (m_running && m_images && !m_still) {
        m_clock.restart();        // the scene's time runs only while it is drawn
        m_driver->start();
    } else {
        m_driver->stop();
        m_timer.stop();
    }
}

void MotionScene::setStill(bool still)
{
    if (still == m_still)
        return;
    m_still = still;
    emit runningChanged();
    if (m_still) {
        QStringList focus;
        for (const QString &name : std::as_const(m_pending)) {
            if (name.startsWith(QLatin1String("focus.")))
                focus << name;
        }
        m_pending = focus;
        m_driver->stop();
        m_timer.stop();
        if (m_images)
            tick();    // one frame, held
    } else if (m_running && m_images) {
        m_clock.restart();
        m_driver->start();
    }
}

// ------------------------------------------------------------ loading

void MotionScene::reload()
{
    m_timer.stop();
    ++m_generation;
    {
        std::lock_guard<std::mutex> lock(m_mutex);   // the composer reads them
        m_images.reset();
    }
    m_imageIndex.clear();
    if (m_drawn || m_failed) {
        m_drawn = m_failed = false;
        emit drawnChanged();
    }
    update();
    if (m_motion.isEmpty() || m_root.isEmpty())
        return;
    // Gone (the card released meanwhile): nothing is read. An empty root
    // would also make every absolute path look as if it were inside it.
    const QString root = QDir(m_root).canonicalPath();
    if (root.isEmpty()) {
        fail(QStringLiteral("its export %1 is not there").arg(m_root));
        return;
    }
    // Nothing to draw (the checker refuses such a motion, shape_motion_empty):
    // the v1 world is drawn, not an empty scene in its place.
    if (m_motion.value(QStringLiteral("planes")).toList().isEmpty()
            && m_motion.value(QStringLiteral("actors")).toMap().isEmpty()
            && m_motion.value(QStringLiteral("emitters")).toMap().isEmpty()
            && m_motion.value(QStringLiteral("lights")).toMap().isEmpty()) {
        fail(QStringLiteral("it declares nothing to draw"));
        return;
    }
    // A console package's: the export must be its insertion's own.
    if (!m_insertion.isEmpty()) {
        const QRegularExpressionMatch name = kExportName.match(QDir(root).dirName());
        if (!name.hasMatch() || name.captured(1) != m_insertion) {
            fail(QStringLiteral("its export %1 is not insertion %2's").arg(m_root, m_insertion));
            return;
        }
    }

    // Every image the motion names, once, from inside the exported package.
    QStringList names;
    auto want = [&](const QVariant &value) {
        const QString name = value.toString();
        if (!name.isEmpty() && !names.contains(name))
            names << name;
    };
    for (const QVariant &plane : m_motion.value("planes").toList())
        want(plane.toMap().value("image"));
    const QVariantMap sheets = m_motion.value("sheets").toMap();
    for (const QVariant &sheet : sheets)
        want(sheet.toMap().value("image"));
    const QVariantMap lights = m_motion.value("lights").toMap();
    for (const QVariant &light : lights)
        want(light.toMap().value("image"));
    for (int i = 0; i < names.size(); ++i)
        m_imageIndex.insert(names.at(i), i);

    // The previous load first: it removes its marker when it ends.
    if (m_loader.joinable())
        m_loader.join();
    // A console package's images are decoded only once the marker naming
    // its insertion is written: if decoding takes the shell down, the next
    // start skips this insertion's motion (Shape). Without an insertion (the
    // laboratory's scenes) there is no marker.
    QString marker;
    if (!m_insertion.isEmpty()) {
        marker = decodingMarker();
        QString why;
        if (!writeDecodingMarker(marker, m_insertion, &why)) {
            fail(QStringLiteral("the decoding marker could not be written (%1)").arg(why));
            return;
        }
    }
    const quint64 generation = m_generation;
    QPointer<MotionScene> self(this);
    m_loader = std::thread([self, generation, root, names, marker] {
        auto images = std::make_shared<std::vector<QImage>>();
        QString failure;
        qint64 bytes = 0;
        for (const QString &name : names) {
            const QString path = QFileInfo(root + QLatin1Char('/') + name).canonicalFilePath();
            // Inside the export only (the checker already refused unsafe paths).
            if (path.isEmpty() || !path.startsWith(root + QLatin1Char('/'))) {
                failure = QStringLiteral("%1 is not in its export").arg(name);
                break;
            }
            // Its chunks before any reader (pngchunks.h): a reader keeps,
            // and inflates, every text chunk it meets, which no limit of
            // Qt's covers.
            QFile file(path);
            if (!file.open(QIODevice::ReadOnly)) {
                failure = QStringLiteral("%1 could not be decoded (%2)").arg(name, file.errorString());
                break;
            }
            if (!pngchunks::withinBounds(file)) {
                failure = QStringLiteral("%1 could not be decoded (more ancillary data, text or profile, than the "
                                         "console accepts)").arg(name);
                break;
            }
            // As PNG and nothing else, and its header before its pixels, as
            // every other image of a package: its sides within a motion
            // image's, its pixels within what the motion may still decode.
            QImageReader reader(&file, "png");
            const QSize size = reader.size();
            if (!size.isValid() || size.width() < 1 || size.height() < 1 || size.width() > kMotionMaxSide
                || size.height() > kMotionMaxSide) {
                failure = QStringLiteral("%1 could not be decoded (not a PNG within %2x%2)")
                              .arg(name)
                              .arg(kMotionMaxSide);
                break;
            }
            if (bytes + qint64(size.width()) * size.height() * 4 > kMotionDecodedMaxBytes) {
                failure = QStringLiteral("its images decode to more than %1 bytes").arg(kMotionDecodedMaxBytes);
                break;
            }
            QImage image = reader.read();
            if (image.isNull()) {
                failure = QStringLiteral("%1 could not be decoded (%2)").arg(name, reader.errorString());
                break;
            }
            image = image.convertToFormat(QImage::Format_ARGB32_Premultiplied);
            bytes += image.sizeInBytes();
            if (image.isNull() || bytes > kMotionDecodedMaxBytes) {
                failure = QStringLiteral("its images decode to more than %1 bytes").arg(kMotionDecodedMaxBytes);
                break;
            }
            images->push_back(image);
        }
        if (!marker.isEmpty())
            QFile::remove(marker);
        QMetaObject::invokeMethod(self, [self, generation, images, failure] {
            if (self)
                self->loaded(generation, images, failure);
        }, Qt::QueuedConnection);
    });
}

void MotionScene::fail(const QString &why)
{
    qWarning("mun-shell: motion: %s; nothing of it is drawn, the package's v1 look instead", qPrintable(why));
    m_failed = true;
    emit drawnChanged();
}

void MotionScene::loaded(quint64 generation, std::shared_ptr<std::vector<QImage>> images, const QString &failure)
{
    if (generation != m_generation)
        return;
    if (!failure.isEmpty()) {
        fail(failure);
        return;
    }
    // MUN's glow, made here: one image for every glow light.
    QImage glow(256, 256, QImage::Format_ARGB32_Premultiplied);
    glow.fill(Qt::transparent);
    {
        QPainter p(&glow);
        QRadialGradient g(128, 128, 128);
        g.setColorAt(0, QColor(255, 255, 255, 200));
        g.setColorAt(0.5, QColor(255, 255, 255, 60));
        g.setColorAt(1, QColor(255, 255, 255, 0));
        p.fillRect(glow.rect(), g);
    }
    images->push_back(glow);
    {
        std::lock_guard<std::mutex> lock(m_mutex);   // the composer reads them
        m_images = images;
        build();
    }
    qint64 bytes = 0;
    for (const QImage &image : *m_images)
        bytes += image.sizeInBytes();
    qInfo("mun-shell: motion: backend %s, look %dx%d (%s), %d images, %.1f MiB decoded, %d frames a second",
          qPrintable(m_backend), m_resolution.width(), m_resolution.height(), m_nearest ? "whole pixels" : "smooth",
          int(m_images->size()), bytes / 1048576.0, m_rate);
    m_clock.start();
    m_since.start();
    m_stepper = MotionClock(1.0 / m_rate);
    m_simT = 0;
    m_pending.clear();
    m_scheduled.clear();
    m_lateSteps = 0;
    m_totalTicks = 0;
    tick();
    if (m_running && !m_still)
        m_driver->start();
}

void MotionScene::build()
{
    const int glowImage = int(m_images->size()) - 1;
    const QVariantMap look = m_motion.value("look").toMap();
    const QVariantList resolution = look.value("resolution").toList();
    m_resolution = resolution.size() == 2 ? QSize(int(number(resolution.at(0))), int(number(resolution.at(1))))
                                          : QSize(int(kCanvasWidth), int(kCanvasHeight));
    m_nearest = look.value("sampling").toString() == QLatin1String("nearest");
    m_k = kCanvasWidth / std::max(1, m_resolution.width());
    m_rate = std::clamp(int(number(m_motion.value("rate"), 20)), 10, 30);
    const QVariantMap current = m_motion.value("current").toMap();
    m_current = QPointF(number(current.value("x")), number(current.value("y")));
    m_gust = number(current.value("gust"));
    m_gustPeriod = std::max<qreal>(2, number(current.value("period"), 12));
    const QVariantMap depth = m_motion.value("depth").toMap();
    m_hasDepth = !depth.isEmpty();
    m_depthFar = std::clamp<qreal>(number(depth.value("far"), 1), 0.05, 1);
    m_depthSpan = std::max<qreal>(1, number(depth.value("span"), 2000));
    const QVariantMap sway = m_motion.value("sway").toMap();
    m_hasSway = !sway.isEmpty();
    m_swayAmount = QPointF(number(sway.value("x")), number(sway.value("y")));
    const QVariantList swayPeriod = sway.value("period").toList();
    m_swayPeriod = QPointF(std::max<qreal>(1, number(swayPeriod.value(0), 56)), std::max<qreal>(1, number(swayPeriod.value(1), 69)));
    m_swayPivot = std::clamp<qreal>(number(sway.value("pivot"), 0.5), 0.01, 1);
    m_random.seed(1969);
    std::uniform_real_distribution<qreal> unit(0, 1);

    m_planes.clear();
    for (const QVariant &v : m_motion.value("planes").toList()) {
        const QVariantMap p = v.toMap();
        Plane plane;
        plane.image = m_imageIndex.value(p.value("image").toString(), -1);
        plane.z = number(p.value("z"), 0.5);
        plane.at = pointOf(p.value("at"));
        const QVariantMap drift = p.value("drift").toMap();
        plane.drift = QPointF(number(drift.value("x")), number(drift.value("y")));
        plane.vary = varyOf(p.value("vary"));
        m_planes.push_back(plane);
    }

    m_sheets.clear();
    const QVariantMap sheets = m_motion.value("sheets").toMap();
    for (auto it = sheets.cbegin(); it != sheets.cend(); ++it) {
        const QVariantMap s = it.value().toMap();
        Sheet sheet;
        sheet.image = m_imageIndex.value(s.value("image").toString(), -1);
        const QVariantList frame = s.value("frame").toList();
        sheet.frame = QSize(int(number(frame.value(0), 1)), int(number(frame.value(1), 1)));
        const QVariantList size = s.value("size").toList();
        sheet.columns = std::max(1, int(number(size.value(0), sheet.frame.width())) / std::max(1, sheet.frame.width()));
        sheet.views = std::max(1, int(number(s.value("views"), 1)));
        const QVariantMap clips = s.value("clips").toMap();
        for (auto c = clips.cbegin(); c != clips.cend(); ++c) {
            const QVariantMap m = c.value().toMap();
            const QString mode = m.value("mode").toString();
            sheet.clips.insert(c.key(), Clip{int(number(m.value("first"))), std::max(1, int(number(m.value("count"), 1))),
                                             std::max<qreal>(0.5, number(m.value("fps"), 8)),
                                             mode == QLatin1String("once"), mode == QLatin1String("blend")});
        }
        m_sheets.insert(it.key(), sheet);
    }

    m_states.clear();
    const QVariantMap states = m_motion.value("states").toMap();
    for (auto it = states.cbegin(); it != states.cend(); ++it) {
        const QVariantMap s = it.value().toMap();
        State state;
        state.kind = s.value("kind").toString();
        state.path = s.value("path").toString();
        state.clip = s.value("clip").toString();
        state.then = s.value("then").toString();
        state.vary = varyOf(s.value("vary"));
        state.hidden = s.value("hidden").toBool();
        state.onlyVisible = s.value("only").toString() == QLatin1String("visible");
        state.on = s.value("on", true).toBool();
        state.burst = int(number(s.value("burst")));
        state.rate = number(s.value("rate"), 1);
        state.opacity = s.value("opacity").isNull() ? -1 : number(s.value("opacity"), -1);
        state.seconds = s.value("seconds").isNull() ? -1 : number(s.value("seconds"), -1);
        const QVariantMap surge = s.value("surge").toMap();
        state.surge = number(surge.value("amount"));
        state.surgeAt = number(surge.value("at"), 0.25);
        const QVariantMap glide = s.value("glide").toMap();
        if (!glide.isEmpty()) {
            state.glideClip = glide.value("clip").toString();
            const QVariantList every = glide.value("every").toList();
            state.glideEvery = QPoint(std::max(1, int(number(every.value(0), 2))), std::max(1, int(number(every.value(1), 3))));
            state.glideSeconds = pointOf(glide.value("seconds"));
        }
        m_states.insert(it.key(), state);
    }

    m_actors.clear();
    const QVariantMap actors = m_motion.value("actors").toMap();
    for (auto it = actors.cbegin(); it != actors.cend(); ++it) {
        const QVariantMap a = it.value().toMap();
        Actor actor;
        actor.name = it.key();
        actor.sheet = a.value("sheet").toString();
        actor.clip = actor.clipNow = a.value("clip").toString();
        actor.rest = actor.state = a.value("rest").toString();
        actor.keep = a.value("keep").toString();
        actor.at = actor.pos = actor.prevPos = pointOf(a.value("at"));
        actor.z = actor.zNow = actor.prevZ = number(a.value("z"), 0.5);
        actor.mirror = a.value("face").toString() == QLatin1String("mirror");
        actor.views = a.value("face").toString() == QLatin1String("views");
        const QVariantMap avoid = a.value("avoid").toMap();
        actor.avoid = avoid.value("actor").toString();
        actor.avoidRadius = number(avoid.value("radius"));
        actor.avoidPush = number(avoid.value("push"));
        actor.bank = number(a.value("bank"));
        actor.turnClip = a.value("turn").toString();
        const QVariantMap bend = a.value("bend").toMap();
        if (!bend.isEmpty()) {
            actor.bends = true;
            actor.bendAmount = number(bend.value("amount"), 12);
            actor.bendPeriod = std::max<qreal>(0.5, number(bend.value("period"), 5));
            actor.bendWave = number(bend.value("wave"), 0.4);
            actor.bendFollow = number(bend.value("follow"), 0.5);
        }
        const QVariantMap copies = a.value("copies").toMap();
        actor.copies = copies.isEmpty() ? 1 : std::max(1, int(number(copies.value("count"), 1)));
        actor.spread = pointOf(copies.value("spread"));
        actor.lag = number(copies.value("lag"));
        actor.wobble = pointOf(copies.value("wobble"));
        actor.copyClips = copies.value("clips").toStringList();
        const QVariantList places = copies.value("places").toList();
        const qreal spreadDepth = number(copies.value("depth"));
        if (!places.isEmpty()) {
            actor.copies = int(places.size());
            for (const QVariant &place : places) {
                const QVariantList d = place.toList();
                actor.offsets.push_back(QVector3D(float(number(d.value(0))), float(number(d.value(1))), float(number(d.value(2)))));
            }
        } else {
            // the first at the actor's place, the others scattered (seeded);
            // depth drawn only when asked for, so older scenes keep theirs
            for (int i = 0; i < actor.copies; ++i) {
                if (i == 0) {
                    actor.offsets.push_back(QVector3D());
                    continue;
                }
                const qreal dx = (unit(m_random) - 0.5) * actor.spread.x();
                const qreal dy = (unit(m_random) - 0.5) * actor.spread.y();
                const qreal dz = spreadDepth > 0 ? (unit(m_random) - 0.5) * 2 * spreadDepth : 0;
                actor.offsets.push_back(QVector3D(float(dx), float(dy), float(dz)));
            }
        }
        m_actors.push_back(actor);
    }

    m_emitters.clear();
    const QVariantMap emitters = m_motion.value("emitters").toMap();
    for (auto it = emitters.cbegin(); it != emitters.cend(); ++it) {
        const QVariantMap e = it.value().toMap();
        Emitter emitter;
        emitter.name = it.key();
        emitter.sheet = e.value("sheet").toString();
        emitter.clip = e.value("clip").toString();
        emitter.keep = e.value("keep").toString();
        if (e.contains("region")) {
            const QVariantList r = e.value("region").toList();
            emitter.source = Emitter::Region;
            emitter.region = QRectF(number(r.value(0)), number(r.value(1)), number(r.value(2)), number(r.value(3)));
        } else if (e.contains("ring")) {
            const QVariantList radii = e.value("ring").toMap().value("radii").toList();
            emitter.source = Emitter::Ring;
            emitter.ringInner = number(radii.value(0));
            emitter.ringOuter = number(radii.value(1));
        } else {
            emitter.source = Emitter::Point;
            emitter.point = pointOf(e.value("at"));
        }
        emitter.towardDisc = e.value("toward").toString() == QLatin1String("disc");
        const QVariantMap pull = e.value("pull").toMap();
        emitter.pull = !pull.isEmpty();
        emitter.pullRadius = number(pull.value("radius"), 600);
        emitter.pullSpeed = number(pull.value("speed"), 120);
        emitter.pullSwirl = number(pull.value("swirl"), 0.5);
        emitter.pullSwallow = number(pull.value("swallow"), 60);
        emitter.swirl = number(e.value("swirl"));
        emitter.rate = number(e.value("rate"));
        emitter.burst = int(number(e.value("burst")));
        emitter.lifeRange = pointOf(e.value("life"));
        emitter.velocity = pointOf(e.value("velocity"));
        emitter.spread = number(e.value("spread"));
        emitter.gravity = number(e.value("gravity"));
        emitter.sway = number(e.value("sway"));
        emitter.grow = number(e.value("grow"));
        emitter.fade = number(e.value("fade"));
        emitter.z = number(e.value("z"), 0.5);
        emitter.max = std::max(1, int(number(e.value("max"), 16)));
        emitter.on = e.value("on", true).toBool();
        m_emitters.push_back(emitter);
    }

    m_lights.clear();
    const QVariantMap lights = m_motion.value("lights").toMap();
    for (auto it = lights.cbegin(); it != lights.cend(); ++it) {
        const QVariantMap l = it.value().toMap();
        Light light;
        light.name = it.key();
        light.keep = l.value("keep").toString();
        light.image = l.value("image").isNull() ? glowImage : m_imageIndex.value(l.value("image").toString(), -1);
        const QVariant at = l.value("at");
        light.atDisc = at.toString() == QLatin1String("disc");
        light.hasAt = light.atDisc || at.toList().size() == 2;
        light.at = pointOf(at);
        light.radius = number(l.value("radius"), 220);
        light.z = number(l.value("z"), 0.5);
        light.opacity = light.base = number(l.value("opacity"), 1);
        const QVariantMap drift = l.value("drift").toMap();
        light.drift = QPointF(number(drift.value("x")), number(drift.value("y")));
        light.vary = varyOf(l.value("vary"));
        light.on = l.value("on", true).toBool();
        m_lights.push_back(light);
    }

    m_react = m_motion.value("react").toMap();
    const QVariantMap beats = m_motion.value("beats").toMap();
    m_beats = beats.value("list").toList();
    m_beatGap = beats.isEmpty() ? QPointF(6, 18) : pointOf(beats.value("gap"));
    m_beatLast.clear();
    m_nextBeat = m_beatGap.x() + unit(m_random) * (m_beatGap.y() - m_beatGap.x());
    m_focused = m_focusFired = false;
    m_focusLast = -1e9;
    m_launched = m_launchedFocused = m_tooMany = false;
    // An actor whose rest is a state of its own starts in it (hidden, on a
    // path, varying).
    for (Actor &actor : m_actors) {
        if (actor.rest != QLatin1String("idle"))
            startState(actor, actor.rest, 0, false);
    }
}

// ------------------------------------------------------------ behaviour

void MotionScene::event(const QString &name)
{
    if (!m_images)
        return;
    // Applied at the next step (see tick). Steps stop while the scene is
    // not drawn or held still, so at most one of each waits: the focus's
    // latest, which says where it is now.
    const bool focus = name.startsWith(QLatin1String("focus."));
    m_pending.removeAll(name);
    if (focus) {
        m_pending.removeAll(QStringLiteral("focus.play"));
        m_pending.removeAll(QStringLiteral("focus.away"));
    }
    // Held still, a moment's reaction has passed by the time it moves
    // again; where the focus is, has not.
    if (m_still && !focus)
        return;
    m_pending.append(name);
}

void MotionScene::eventAt(const QString &name, int step)
{
    if (m_images)
        m_scheduled.emplace_back(step, name);
}

QVariantMap MotionScene::actorAt(const QString &name) const
{
    for (const Actor &actor : m_actors) {
        if (actor.name == name)
            return {{"x", actor.pos.x()}, {"y", actor.pos.y()}, {"z", actor.zNow}, {"state", actor.state},
                    {"hidden", actor.hidden}};
    }
    return {};
}

void MotionScene::applyEvent(const QString &name, qreal t)
{
    // One launch until it is refused or the identity comes again: the
    // console may say it twice as the launcher accepts.
    if (name == QLatin1String("launch") && m_launched)
        return;
    qInfo("mun-shell: motion: %s at step %d (%.2f s)", qPrintable(name), int(std::lround(t * m_rate)), t);
    if (name == QLatin1String("launch")) {
        // A focus reaction still waiting for its settle does not come
        // after the launch's (it would undo the dive).
        m_launched = true;
        m_launchedFocused = m_focused && m_focusFired;
        m_focusFired = true;
    } else if (name == QLatin1String("launch.refused")) {
        if (!m_launched)
            return;
        m_launched = false;
        restore(m_react.value(QStringLiteral("launch")).toMap().value("do").toMap(), t);
        if (m_focused) {
            const QVariantMap focus = m_react.value(QStringLiteral("focus.play")).toMap();
            if (m_launchedFocused)
                applyTargets(focus.value("do").toMap(), t, true);
            else
                m_focusFired = false, m_focusSince = t;   // it settles again
        }
        return;
    } else if (name == QLatin1String("return") || name == QLatin1String("arrive")) {
        m_launched = false;
    }
    if (name == QLatin1String("focus.play")) {
        if (!m_focused) {
            m_focused = true;
            m_focusSince = t;
            m_focusFired = false;
        }
        return;
    }
    if (name == QLatin1String("focus.away")) {
        // Actors still in Play's reaction go back to rest first; then the
        // package's own focus.away reaction applies, so that what it names
        // (a light, an emitter, an actor) takes the state it declares.
        m_focused = false;
        for (Actor &actor : m_actors) {
            if (actor.reacting)
                goHome(actor, t);
        }
    }
    const QVariantMap reaction = m_react.value(name).toMap();
    if (!reaction.isEmpty())
        applyTargets(reaction.value("do").toMap(), t, false);
}

void MotionScene::restore(const QVariantMap &moves, qreal t)
{
    const QVariantMap emitters = m_motion.value("emitters").toMap();
    const QVariantMap lights = m_motion.value("lights").toMap();
    for (auto it = moves.cbegin(); it != moves.cend(); ++it) {
        for (Actor &actor : m_actors) {
            if (actor.name == it.key())
                goHome(actor, t);
        }
        for (Emitter &emitter : m_emitters) {
            if (emitter.name == it.key()) {
                emitter.on = emitters.value(it.key()).toMap().value("on", true).toBool();
                emitter.rateFactor = 1;
            }
        }
        for (Light &light : m_lights) {
            if (light.name == it.key()) {
                light.on = lights.value(it.key()).toMap().value("on", true).toBool();
                light.target = -1;
            }
        }
    }
}

void MotionScene::applyTargets(const QVariantMap &moves, qreal t, bool reaction)
{
    std::uniform_real_distribution<qreal> unit(0, 1);
    for (auto it = moves.cbegin(); it != moves.cend(); ++it) {
        const QString who = it.key(), what = it.value().toString();
        for (Actor &actor : m_actors) {
            if (actor.name == who)
                startState(actor, what, t, reaction);
        }
        for (Emitter &emitter : m_emitters) {
            if (emitter.name != who)
                continue;
            if (what == QLatin1String("on") || what == QLatin1String("off")) {
                emitter.on = what == QLatin1String("on");
            } else {
                const State state = m_states.value(who + QLatin1Char('.') + what);
                const int burst = what == QLatin1String("burst") ? std::max(1, emitter.burst) : state.burst;
                if (what != QLatin1String("burst")) {
                    emitter.on = state.on;
                    emitter.rateFactor = state.rate;
                }
                for (int i = 0; i < burst && int(emitter.particles.size()) < emitter.max; ++i)
                    emitter.accumulated += 1;
            }
        }
        for (Light &light : m_lights) {
            if (light.name != who)
                continue;
            if (what == QLatin1String("on") || what == QLatin1String("off")) {
                light.on = what == QLatin1String("on");
                light.target = -1;    // back to its own opacity
            } else {
                const State state = m_states.value(who + QLatin1Char('.') + what);
                light.on = state.on;
                light.target = state.opacity;
            }
        }
    }
    Q_UNUSED(unit);
}

qreal MotionScene::depthScale(qreal z) const
{
    return m_hasDepth ? 1 / (1 + z * (1 / m_depthFar - 1)) : 1;
}

// How far the viewpoint's drift moves what is at depth z: all of it at
// depth 0, none at the pivot, the other way beyond, by perspective (in
// proportion without it).
QPointF MotionScene::swayAt(qreal t, qreal z) const
{
    if (!m_hasSway)
        return {};
    const qreal pivot = m_hasDepth ? depthScale(m_swayPivot) : 1 - m_swayPivot;
    const qreal here = m_hasDepth ? depthScale(z) : 1 - z;
    const qreal share = (here - pivot) / std::max<qreal>(0.01, 1 - pivot);
    return QPointF(m_swayAmount.x() * std::sin(kTau * t / m_swayPeriod.x()),
                   m_swayAmount.y() * std::sin(kTau * t / m_swayPeriod.y() + 1.1)) * share;
}

void MotionScene::setCourse(Actor &actor, std::vector<QVector3D> points, qreal seconds, qreal speed,
                            const QString &ease, bool loop, bool smooth, const QString &after, qreal t, bool even)
{
    // An actor that turns to face its way, facing away from where a course
    // sets off, swims on a little and comes round (a lead point ahead of it
    // and up) rather than backing away.
    if (actor.mirror && !loop && points.size() >= 2 && (points[1].x() - points[0].x()) * actor.facing < -40)
        points.insert(points.begin() + 1, points[0] + QVector3D(70 * actor.facing, -55, 0));
    // A smooth course leaves the way the actor was already going.
    const QVector3D ghost = at3(actor.pos - actor.velocity * 0.6, actor.zNow - actor.vz * 0.6);
    const bool lead = !loop && std::hypot(actor.velocity.x(), actor.velocity.y()) > 10;
    std::vector<qreal> params;     // for an even pace: which point each one of the course is near
    if (smooth && points.size() >= 2) {
        actor.course = smoothed(points, lead ? &ghost : nullptr, loop, &params);
    } else {
        actor.course = std::move(points);
        if (loop && actor.course.size() > 1)
            actor.course.push_back(actor.course.front());
        for (size_t i = 0; i < actor.course.size(); ++i)
            params.push_back(qreal(i));
    }
    // Its length counts depth by perspective: going away is going as far
    // as going across, at that depth (scene.depth's span).
    actor.courseAt.assign(actor.course.size(), 0);
    for (size_t i = 1; i < actor.course.size(); ++i) {
        const QVector3D d = actor.course[i] - actor.course[i - 1];
        const qreal across = m_hasDepth ? d.z() * m_depthSpan * depthScale(actor.course[i].z()) : 0;
        actor.courseAt[i] = actor.courseAt[i - 1] + std::sqrt(qreal(d.x()) * d.x() + qreal(d.y()) * d.y() + across * across);
    }
    actor.courseLength = actor.courseAt.empty() ? 0 : actor.courseAt.back();
    actor.join = QVector3D();
    // An even pace: every point reached in the same time, whatever the
    // distance between them (a motion sampled at even times keeps its own
    // rhythm). Its "distance" is then the points passed, in proportion.
    if (even && params.size() == actor.course.size() && !params.empty() && params.back() > 0) {
        const qreal unit = actor.courseLength / params.back();
        for (size_t i = 0; i < params.size(); ++i)
            actor.courseAt[i] = params[i] * unit;
    }
    actor.courseDone = 0;
    // A loop is joined where it comes nearest: the actor goes round from
    // there, what lies between it and the loop fading out over a second or
    // so (no jump, and the loop stays the package's).
    if (loop && !actor.course.empty()) {
        const QVector3D here = at3(actor.pos, actor.zNow);
        const qreal across = m_hasDepth ? m_depthSpan * depthScale(actor.zNow) : 0;
        size_t nearest = 0;
        qreal best = 1e18;
        for (size_t i = 0; i < actor.course.size(); ++i) {
            const QVector3D d = actor.course[i] - here;
            const qreal dz = d.z() * across;
            const qreal distance = qreal(d.x()) * d.x() + qreal(d.y()) * d.y() + dz * dz;
            if (distance < best) {
                best = distance;
                nearest = i;
            }
        }
        actor.courseDone = actor.courseAt.empty() ? 0 : actor.courseAt[nearest];
        actor.join = here - actor.course[nearest];
    }
    actor.courseStart = t;
    actor.courseEase = loop ? QStringLiteral("linear") : ease;     // a loop never slows to a stop
    actor.courseLoop = loop;
    actor.after = after;
    // The speed that covers it in about `seconds`, allowing for its eased ends.
    const qreal easing = actor.courseEase == QLatin1String("inout") ? 1.25
                         : actor.courseEase == QLatin1String("linear") ? 1.0 : 1.12;
    actor.courseSpeed = seconds > 0 ? actor.courseLength / seconds * easing : std::max<qreal>(1, speed);
}

void MotionScene::switchClip(Actor &actor, const QString &name, qreal t)
{
    if (name.isEmpty() || name == actor.clipNow)
        return;
    const Sheet sheet = m_sheets.value(actor.sheet);
    const Clip from = sheet.clips.value(actor.clipNow, sheet.clips.value(actor.clip));
    const Clip to = sheet.clips.value(name);
    const int shown = frameOf(actor, from, t, 0);
    actor.clipNow = name;
    // A clip that holds the frame on screen carries on from it: no jump.
    actor.clipSince = shown >= to.first && shown < to.first + to.count ? t - (shown - to.first + 0.5) / to.fps : t;
    actor.lastCycle = int(std::floor((t - actor.clipSince) * to.fps / to.count));
}

int MotionScene::frameOf(const Actor &actor, const Clip &clip, qreal t, int copy) const
{
    int frame = int(std::floor((t - actor.clipSince + copy * actor.lag) * clip.fps));
    frame = clip.once ? std::clamp(frame, 0, clip.count - 1) : ((frame % clip.count) + clip.count) % clip.count;
    return clip.first + frame;
}

QPointF MotionScene::currentAt(qreal t) const
{
    const qreal gust = 0.6 * std::sin(kTau * t / m_gustPeriod) + 0.4 * std::sin(kTau * t / (m_gustPeriod * 0.37) + 1.3);
    return m_current * (1 + m_gust * gust);
}

void MotionScene::startState(Actor &actor, const QString &label, qreal t, bool reaction)
{
    if (label == QLatin1String("idle")) {
        goHome(actor, t);
        return;
    }
    const auto found = m_states.constFind(actor.name + QLatin1Char('.') + label);
    if (found == m_states.cend())
        return;
    const State &state = *found;
    if (state.onlyVisible && actor.hidden)
        return;
    actor.state = label;
    actor.reacting = reaction;
    actor.returning = false;
    actor.hidden = state.hidden;
    actor.stateUntil = -1;
    actor.gliding = false;
    switchClip(actor, state.clip.isEmpty() ? actor.clip : state.clip, t);
    if (!state.glideClip.isEmpty()) {
        std::uniform_int_distribution<int> strokes(state.glideEvery.x(), std::max(state.glideEvery.x(), state.glideEvery.y()));
        actor.strokesLeft = strokes(m_random);
    }
    const QString after = !state.then.isEmpty() ? state.then : reaction ? QStringLiteral("hold") : QStringLiteral("rest");
    actor.after = after;
    if (state.path.isEmpty()) {
        // No path: a once clip plays out, a timed state lasts its seconds,
        // then `then` (or rest); otherwise the state holds.
        actor.course.clear();
        const Clip clip = m_sheets.value(actor.sheet).clips.value(actor.clipNow);
        if (clip.once)
            actor.stateUntil = t + clip.count / clip.fps;
        else if (state.seconds > 0)
            actor.stateUntil = t + state.seconds;
        return;
    }
    const QVariantMap path = m_motion.value("paths").toMap().value(state.path).toMap();
    std::vector<QVector3D> points;
    // a course starts where the actor is; a loop is joined instead (setCourse)
    const bool loop = path.value("loop").toBool();
    if (!loop || path.value("from").toString() == QLatin1String("disc"))
        points.push_back(at3(path.value("from").toString() == QLatin1String("disc") ? m_disc : actor.pos, actor.zNow));
    const QVariantMap around = path.value("around").toMap();
    if (!around.isEmpty()) {
        const QPointF radius = pointOf(around.value("radius"));
        const qreal turns = number(around.value("turns"), 1);
        const qreal start = std::atan2((actor.pos.y() - m_disc.y()) / std::max<qreal>(1, radius.y()),
                                       (actor.pos.x() - m_disc.x()) / std::max<qreal>(1, radius.x()));
        const int steps = std::max(8, int(std::abs(turns) * 24));
        for (int i = 0; i <= steps; ++i) {
            const qreal a = start + kTau * turns * i / steps;
            points.push_back(at3(m_disc + QPointF(std::cos(a) * radius.x(), std::sin(a) * radius.y()), actor.zNow));
        }
    }
    // Points measured from the disc (`origin`), where it is now: the course
    // keeps its shape wherever Home draws the card object.
    const QPointF origin = path.value("origin").toString() == QLatin1String("disc") ? m_disc : QPointF();
    for (const QVector3D &p : pointsOf(path.value("points"), actor.zNow))
        points.push_back(p + QVector3D(float(origin.x()), float(origin.y()), 0));
    if (path.value("to").toString() == QLatin1String("disc"))
        points.push_back(at3(m_disc, actor.zNow));
    const QVariant seconds = path.value("seconds");
    setCourse(actor, points, seconds.isNull() ? -1 : number(seconds, -1), number(path.value("speed"), 120),
              path.value("ease").toString(), path.value("loop").toBool(), path.value("smooth", true).toBool(), after, t,
              path.value("pace").toString() == QLatin1String("even"));
}

void MotionScene::goHome(Actor &actor, qreal t)
{
    actor.reacting = false;
    actor.returning = true;
    actor.hidden = false;
    actor.stateUntil = -1;
    // It travels home in its rest's rhythm.
    actor.state = actor.rest;
    const State rest = m_states.value(actor.name + QLatin1Char('.') + actor.rest);
    actor.gliding = false;
    switchClip(actor, rest.clip.isEmpty() ? actor.clip : rest.clip, t);
    setCourse(actor, {at3(actor.pos, actor.zNow), at3(actor.at, actor.z)}, -1, kHomeSpeed, QStringLiteral("inout"), false, true,
              QStringLiteral("rest"), t, false);
}

// One actor's tick: its swim's rhythm, its way along its course, the end of
// a course or a timed state, and where it faces and how it pitches.
//
// The stroke's first frame is its rest pose: a glide starts and ends on it
// (the glide clip's first frame is that pose), and a turn starts on it and
// ends ready for it (the turn clip strokes on as it turns). So nothing
// changes pose at once: a turn waits for that pose, wanted early enough by
// looking along the course, and never comes again within 1.5 s.
void MotionScene::moveActor(Actor &actor, qreal t, qreal dt)
{
    const State state = m_states.value(actor.name + QLatin1Char('.') + actor.state);
    const Sheet sheet = m_sheets.value(actor.sheet);
    const QString main = state.clip.isEmpty() ? actor.clip : state.clip;
    std::uniform_real_distribution<qreal> unit(0, 1);
    const Clip stroke = sheet.clips.value(main);
    const qreal cycle = stroke.count / std::max<qreal>(0.5, stroke.fps);
    // where the clip on show is in its cycle (0..1), and whether it is on its first frame
    const Clip shown = sheet.clips.value(actor.clipNow);
    const qreal phase = std::fmod(std::max<qreal>(0, t - actor.clipSince) * shown.fps / shown.count, 1.0);
    const bool firstPose = int(std::floor(phase * shown.count)) == 0;

    // Strokes and glides.
    if (!state.glideClip.isEmpty() && !actor.turning) {
        if (actor.gliding) {
            // ends after its seconds, when its clip comes round to its first frame
            if (t >= actor.glideUntil && firstPose) {
                actor.gliding = false;
                actor.clipNow = main;
                actor.clipSince = t;
                actor.lastCycle = 0;
                std::uniform_int_distribution<int> strokes(state.glideEvery.x(), std::max(state.glideEvery.x(), state.glideEvery.y()));
                actor.strokesLeft = strokes(m_random);
            }
        } else {
            const int done = int(std::floor((t - actor.clipSince) * shown.fps / shown.count));
            if (done > actor.lastCycle) {
                actor.strokesLeft -= done - actor.lastCycle;
                actor.lastCycle = done;
            }
            if (actor.strokesLeft <= 0 && firstPose && actor.clipNow == main && !actor.turnPending) {
                actor.gliding = true;
                actor.glideUntil = t + state.glideSeconds.x()
                                   + unit(m_random) * std::max<qreal>(0, state.glideSeconds.y() - state.glideSeconds.x());
                actor.clipNow = state.glideClip;
                actor.clipSince = t;
            }
        }
    } else if (actor.gliding && !actor.turning) {
        actor.gliding = false;
        switchClip(actor, main, t);
    }
    // a glide coasts down slowly; a stroke picks up again quicker
    actor.speedScale += ((actor.gliding ? 0.3 : 1.0) - actor.speedScale) * follow(dt, actor.gliding ? 1.6 : 0.45);

    // Which way the course goes from here: over the next ~1.2 s of it (a
    // loop wraps), sideways by more than 30 px to count.
    int ahead = 0, now = 0;
    if (!actor.course.empty() && actor.courseLength > 0) {
        const qreal reach = std::clamp<qreal>(actor.courseSpeed * 1.2, 60, 220);
        auto at = [&](qreal d) {
            if (actor.courseLoop)
                d = std::fmod(d, actor.courseLength);
            return alongDistance(actor.course, actor.courseAt, d);
        };
        const qreal far = qreal(at(actor.courseDone + reach).x()) - actor.pos.x();
        const qreal near = qreal(at(actor.courseDone + 20).x()) - actor.pos.x();
        ahead = far > 30 ? 1 : far < -30 ? -1 : 0;
        now = near > 2 ? 1 : near < -2 ? -1 : 0;
    }

    const QPointF before = actor.pos;
    const qreal beforeZ = actor.zNow;
    auto finish = [&](const QString &after) {
        actor.course.clear();
        actor.stateUntil = -1;
        if (after == QLatin1String("hold")) {
            // stays while the focus does
        } else if (after == QLatin1String("rest")) {
            actor.returning = actor.reacting = false;
            actor.state = actor.rest;
            if (actor.rest != QLatin1String("idle"))
                startState(actor, actor.rest, t, false);
            else
                switchClip(actor, actor.clip, t);
        } else {
            startState(actor, after, t, actor.reacting);
        }
    };
    if (!actor.course.empty() && actor.courseLength > 0) {
        qreal f = actor.speedScale;
        if (state.surge > 0 && !actor.gliding && !actor.turning && shown.count > 1)
            // fastest where the clip says (`at`), the push of the stroke before it
            f *= 1 + state.surge * std::cos(kTau * (phase - state.surgeAt));
        if (actor.turning)
            f *= 0.55;
        // never backwards: facing away from where the course goes now, it
        // all but stops until it has turned
        if (actor.mirror && now != 0 && now != actor.facing && !(actor.turning && t - actor.turnSince > 0.5 * cycle))
            f *= 0.08;
        if (!actor.courseLoop) {
            const qreal reach = std::min<qreal>(140, actor.courseLength / 3);
            const bool easeIn = actor.courseEase == QLatin1String("in") || actor.courseEase == QLatin1String("inout");
            const bool easeOut = actor.courseEase == QLatin1String("out") || actor.courseEase == QLatin1String("inout");
            if (easeIn)
                f *= 0.2 + 0.8 * smooth01(actor.courseDone / reach);
            if (easeOut)
                f *= 0.15 + 0.85 * smooth01((actor.courseLength - actor.courseDone) / reach);
        }
        actor.courseDone += actor.courseSpeed * std::max<qreal>(0.03, f) * dt;
        if (actor.courseLoop)
            actor.courseDone = std::fmod(actor.courseDone, actor.courseLength);
        actor.join *= float(1 - follow(dt, 0.9));
        const QVector3D here = alongDistance(actor.course, actor.courseAt, actor.courseDone) + actor.join;
        actor.pos = here.toPointF();
        actor.zNow = here.z();
        if (!actor.courseLoop && actor.courseDone >= actor.courseLength)
            finish(actor.after);
    } else if (!actor.course.empty()) {
        actor.pos = actor.course.front().toPointF();
        actor.zNow = actor.course.front().z();
        finish(actor.after);
    } else if (actor.stateUntil > 0 && t >= actor.stateUntil) {
        finish(actor.after);
    }

    if (dt > 0) {
        const QPointF moved = (actor.pos - before) / dt;
        actor.velocity += (moved - actor.velocity) * follow(dt, 0.25);
        actor.vz += ((actor.zNow - beforeZ) / dt - actor.vz) * follow(dt, 0.25);
    }
    const qreal speed = std::hypot(actor.velocity.x(), actor.velocity.y());

    // Heading (face "views"): the way it moves, across and in depth, eased
    // round the short way; its row changes only once well past halfway to
    // the next, so it never flickers between two.
    if (actor.views) {
        const qreal across = actor.vz * m_depthSpan * depthScale(actor.zNow);
        if (std::hypot(actor.velocity.x(), across) > 6) {
            const qreal want = std::atan2(across, actor.velocity.x());
            const qreal turn = std::remainder(want - actor.yaw, kTau);
            actor.yaw = std::remainder(actor.yaw + turn * follow(dt, 0.35), kTau);
        }
        const int count = std::max(1, m_sheets.value(actor.sheet).views);
        const qreal step = kTau / count;
        const qreal off = std::remainder(actor.yaw - actor.view * step, kTau);
        if (std::abs(off) > step * 0.62)
            actor.view = ((int(std::lround(actor.yaw / step)) % count) + count) % count;
    }

    // Facing: it turns to face where its course goes.
    if (actor.mirror) {
        const int want = ahead != 0 ? ahead : actor.facing;
        if (!actor.turning && !actor.turnPending && want != actor.facing && t - actor.lastTurnEnd > 1.5) {
            actor.turnPending = true;
            actor.turnWantedSince = t;
            actor.turnTo = want;
        }
        if (actor.turnPending && want == actor.facing)
            actor.turnPending = false;       // the course came round again before it turned
        const bool hasTurn = sheet.clips.contains(actor.turnClip);
        // a turn starts on the stroke's first pose (or within a cycle anyway)
        if (actor.turnPending && (!hasTurn || firstPose || t - actor.turnWantedSince > cycle * 1.2)) {
            actor.turnPending = false;
            actor.turning = true;
            actor.turnSince = t;
            actor.gliding = false;
        }
        if (actor.turning) {
            const Clip turn = sheet.clips.value(actor.turnClip);
            const qreal lasts = hasTurn ? turn.count / turn.fps : 0.45;
            if (t - actor.turnSince >= lasts) {
                actor.facing = actor.turnTo;
                actor.turning = false;
                actor.lastTurnEnd = t;
                // the stroke from its first pose, which the turn ends ready for
                actor.clipNow = main;
                actor.clipSince = t;
                actor.lastCycle = 0;
            }
        }
    }
    // Pitch: its nose follows its course up and down, as far as `bank` says.
    if (actor.bank > 0) {
        qreal want = 0;
        if (actor.views) {
            // seen side on it pitches fully; nose or tail on, hardly
            const qreal side = std::cos(actor.yaw);
            if (speed > 12)
                want = std::atan2(actor.velocity.y(), std::max<qreal>(1, std::abs(actor.velocity.x()))) * 180 / M_PI
                       * actor.bank * (side >= 0 ? 1 : -1) * std::abs(side);
        } else if (speed > 12 && !actor.turning)
            want = std::atan2(actor.velocity.y(), std::abs(actor.velocity.x())) * 180 / M_PI * actor.bank * actor.facing;
        actor.pitch += (std::clamp<qreal>(want, -28, 28) - actor.pitch) * follow(dt, 0.6);
    }
}

QString MotionScene::shownClip(const Actor &actor, const QString &clip, int facing) const
{
    if (facing < 0) {
        const QString left = clip + QStringLiteral("-left");
        if (m_sheets.value(actor.sheet).clips.contains(left))
            return left;
    }
    return clip;
}

void MotionScene::runBeat(qreal t)
{
    std::uniform_real_distribution<qreal> unit(0, 1);
    m_nextBeat = t + m_beatGap.x() + unit(m_random) * (m_beatGap.y() - m_beatGap.x());
    auto calm = [this](const QString &who) {
        for (const Actor &actor : m_actors) {
            if (actor.name == who)
                return !actor.reacting && !actor.returning && actor.state == actor.rest;
        }
        return true;
    };
    std::vector<int> eligible;
    qreal total = 0;
    for (int i = 0; i < m_beats.size(); ++i) {
        const QVariantMap beat = m_beats.at(i).toMap();
        const QVariantList every = beat.value("every").toList();
        if (every.size() == 2 && m_beatLast.contains(i) && t - m_beatLast.value(i) < number(every.at(0)))
            continue;
        const QVariantMap moves = beat.value("do").toMap();
        if (!std::all_of(moves.keyBegin(), moves.keyEnd(), calm))
            continue;
        eligible.push_back(i);
        total += std::max<qreal>(1, number(beat.value("weight"), 1));
    }
    if (eligible.empty())
        return;
    qreal pick = unit(m_random) * total;
    for (int i : eligible) {
        const QVariantMap beat = m_beats.at(i).toMap();
        pick -= std::max<qreal>(1, number(beat.value("weight"), 1));
        if (pick <= 0) {
            m_beatLast.insert(i, t);
            applyTargets(beat.value("do").toMap(), t, false);
            return;
        }
    }
}

// Keeping away from another actor: within `radius` (depth counted by
// perspective), pushed straight away from it, up to `push` pixels the
// nearer it is, eased in and out (about 0.4 s), the course unchanged.
void MotionScene::keepAway(Actor &actor, qreal dt)
{
    const auto other = std::find_if(m_actors.cbegin(), m_actors.cend(), [&](const Actor &a) { return a.name == actor.avoid; });
    if (other == m_actors.cend())
        return;
    const qreal perspective = m_hasDepth ? m_depthSpan * depthScale(actor.zNow) : 0;
    const QPointF here = actor.pos + actor.shift.toPointF();
    const qreal dx = here.x() - other->pos.x(), dy = here.y() - other->pos.y();
    const qreal dz = (actor.zNow + actor.shift.z() - other->zNow) * perspective;
    const qreal distance = std::sqrt(dx * dx + dy * dy + dz * dz);
    QVector3D goal;
    if (!other->hidden && distance < actor.avoidRadius && distance > 0.001) {
        const qreal push = actor.avoidPush * (1 - distance / actor.avoidRadius) / distance;
        goal = QVector3D(float(dx * push), float(dy * push), perspective > 0 ? float(dz * push / perspective) : 0.f);
    }
    actor.shift += (goal - actor.shift) * float(follow(dt, 0.4));
}

void MotionScene::simulate(qreal t, qreal dt)
{
    std::uniform_real_distribution<qreal> unit(0, 1);
    const QVariantMap focus = m_react.value(QStringLiteral("focus.play")).toMap();
    if (m_focused && !m_focusFired && !focus.isEmpty() && t - m_focusSince >= number(focus.value("settle"), 0.6)
            && t - m_focusLast >= number(focus.value("cooldown"), 20)) {
        m_focusFired = true;
        m_focusLast = t;
        applyTargets(focus.value("do").toMap(), t, true);
    }
    if (!m_beats.isEmpty() && t >= m_nextBeat)
        runBeat(t);

    for (Actor &actor : m_actors) {
        actor.prevPos = actor.pos;
        actor.prevZ = actor.zNow;
        actor.prevShift = actor.shift;
        moveActor(actor, t, dt);
    }
    for (Actor &actor : m_actors) {
        if (!actor.avoid.isEmpty())
            keepAway(actor, dt);
    }

    const QPointF flow = currentAt(t);
    for (Emitter &emitter : m_emitters) {
        if (emitter.on)
            emitter.accumulated += emitter.rate * emitter.rateFactor * dt;
        while (emitter.accumulated >= 1) {
            emitter.accumulated -= 1;
            if (int(emitter.particles.size()) >= emitter.max)
                continue;
            Particle p;
            if (emitter.source == Emitter::Region) {
                p.pos = QPointF(emitter.region.x() + unit(m_random) * emitter.region.width(),
                                emitter.region.y() + unit(m_random) * emitter.region.height());
            } else if (emitter.source == Emitter::Ring) {
                const qreal a = unit(m_random) * kTau;
                const qreal r = emitter.ringInner + unit(m_random) * (emitter.ringOuter - emitter.ringInner);
                p.pos = m_disc + QPointF(std::cos(a) * r, std::sin(a) * r);
            } else {
                p.pos = emitter.point;
            }
            const qreal turn = (unit(m_random) - 0.5) * emitter.spread * kTau / 360;
            p.velocity = QPointF(emitter.velocity.x() * std::cos(turn) - emitter.velocity.y() * std::sin(turn),
                                 emitter.velocity.x() * std::sin(turn) + emitter.velocity.y() * std::cos(turn));
            p.life = emitter.lifeRange.x() + unit(m_random) * std::max<qreal>(0, emitter.lifeRange.y() - emitter.lifeRange.x());
            p.phase = unit(m_random) * kTau;
            p.prev = p.pos;
            emitter.particles.push_back(p);
        }
        // The current carries what is near more than what is far.
        const QPointF carried = flow * (1.2 - 0.8 * emitter.z);
        for (Particle &p : emitter.particles) {
            p.prev = p.pos;
            p.age += dt;
            if (emitter.pull) {
                // drawn in near the disc, the more the nearer, turning round it
                const QPointF to = m_disc - p.pos;
                const qreal distance = std::max<qreal>(1, std::hypot(to.x(), to.y()));
                if (distance < emitter.pullRadius) {
                    const qreal near = 1 - distance / emitter.pullRadius;
                    const QPointF inward = to / distance, round(-inward.y(), inward.x());
                    p.pos += (inward * near * near + round * near * emitter.pullSwirl) * emitter.pullSpeed * dt;
                    if (distance < emitter.pullSwallow)
                        p.age = p.life;
                }
            }
            if (emitter.towardDisc) {
                const QPointF to = m_disc - p.pos;
                const qreal distance = std::max<qreal>(1, std::hypot(to.x(), to.y()));
                const QPointF inward = to / distance, round(-inward.y(), inward.x());
                p.pos += (inward * 70 + round * 70 * emitter.swirl) * dt;
                if (distance < 40)
                    p.age = p.life;
            } else {
                p.velocity.ry() += emitter.gravity * dt;
                p.pos += (p.velocity + carried) * dt;
                p.pos.rx() += std::sin(p.age * 1.7 + p.phase) * emitter.sway * dt;
            }
        }
        emitter.particles.erase(std::remove_if(emitter.particles.begin(), emitter.particles.end(),
                                               [](const Particle &p) { return p.age >= p.life; }),
                                emitter.particles.end());
    }

    for (Light &light : m_lights) {
        // towards its state's opacity, its own, or none when off; eased
        const qreal goal = !light.on ? 0 : light.target >= 0 ? light.target : light.base;
        light.opacity += (goal - light.opacity) * std::min<qreal>(1, dt * 3);
    }
}

void MotionScene::collect(qreal t, qreal alpha)
{
    struct Ordered { qreal z; int order; DrawItem item; };
    std::vector<Ordered> list;
    int order = 0;
    const qreal k = m_k;
    auto imageSize = [this](int index) {
        return index >= 0 && index < int(m_images->size()) ? (*m_images)[index].size() : QSize();
    };
    const qreal step = m_nearest ? k : 0;
    // What moves is placed on the display's pixels, so it glides rather than
    // jumping a scene pixel at a time; its own pixels stay whole (the
    // composed way draws at the look's resolution and keeps them there).
    const qreal fine = m_nearest ? 1 : 0;
    // Planes keep the scene's pixels, unless the viewpoint drifts: then they
    // glide too, a display pixel at a time.
    const qreal planeStep = m_hasSway ? fine : step;

    for (const Plane &plane : m_planes) {
        const QSize size = imageSize(plane.image);
        if (size.isEmpty())
            continue;
        const qreal w = size.width() * k, h = size.height() * k;
        const QPointF sway = swayAt(t, plane.z);
        qreal x = plane.at.x() + sway.x() + plane.vary.value(QStringLiteral("x"), t, 0, 0);
        const qreal y = plane.at.y() + sway.y() + plane.vary.value(QStringLiteral("y"), t, 0, 0) + plane.drift.y() * t;
        if (plane.drift.x() != 0)
            x += std::fmod(plane.drift.x() * t, w);
        const qreal opacity = plane.vary.value(QStringLiteral("opacity"), t, 0, 1);
        for (qreal copy = x - (x > 0 ? w : 0); copy < kCanvasWidth; copy += w) {
            list.push_back({plane.z, order++, DrawItem{plane.image, QRectF(QPointF(), size),
                                                       QRectF(snap(copy, planeStep), snap(y, planeStep), w, h), opacity, false}});
            if (plane.drift.x() == 0 && copy >= x)
                break;
        }
    }

    for (const Light &light : m_lights) {
        const QSize size = imageSize(light.image);
        if (size.isEmpty() || light.opacity <= 0.01)
            continue;
        const qreal opacity = light.opacity * light.vary.value(QStringLiteral("opacity"), t, 0, 1);
        const QPointF c = light.atDisc ? m_disc : light.at;
        if (light.image == int(m_images->size()) - 1) {          // MUN's glow
            list.push_back({light.z, order++, DrawItem{light.image, QRectF(QPointF(), size),
                                                       QRectF(c.x() - light.radius, c.y() - light.radius, 2 * light.radius,
                                                              2 * light.radius), opacity, false}});
            continue;
        }
        const qreal w = size.width() * k, h = size.height() * k;
        QRectF target = light.hasAt ? QRectF(c.x() - w / 2, c.y() - h / 2, w, h) : QRectF(0, 0, w, h);
        target.translate(swayAt(t, light.z) + QPointF(light.vary.value(QStringLiteral("x"), t, 0, 0),
                                                      light.vary.value(QStringLiteral("y"), t, 0, 0) + light.drift.y() * t));
        // A light that drifts sideways repeats across the screen, as a plane does.
        qreal x = target.x();
        if (light.drift.x() != 0) {
            x += std::fmod(light.drift.x() * t, w);
            while (x > 0)
                x -= w;
        }
        for (; x < kCanvasWidth; x += w) {
            list.push_back({light.z, order++, DrawItem{light.image, QRectF(QPointF(), size),
                                                       QRectF(snap(x, step), snap(target.y(), step), w, h), opacity, false}});
            if (light.drift.x() == 0)
                break;
        }
    }

    const QPointF flow = currentAt(t);
    for (const Actor &actor : m_actors) {
        if (actor.hidden)
            continue;
        const auto sheetIt = m_sheets.constFind(actor.sheet);
        if (sheetIt == m_sheets.cend() || imageSize(sheetIt->image).isEmpty())
            continue;
        const Sheet &sheet = *sheetIt;
        const State state = m_states.value(actor.name + QLatin1Char('.') + actor.state);
        // Facing left it shows a clip's "-left" version as drawn when the
        // sheet has one, else the clip mirrored. Turning: its turn clip (the
        // one for the side it turns from), or a quick turn about its middle,
        // changing side halfway.
        const bool turnClip = actor.turning && sheet.clips.contains(actor.turnClip);
        QString name = actor.clipNow;
        int side = actor.facing;
        qreal squash = 1;
        if (actor.turning && turnClip) {
            name = actor.turnClip;
        } else if (actor.turning) {
            const qreal p = std::clamp<qreal>((t - actor.turnSince) / 0.45, 0, 1);
            squash = std::max<qreal>(0.08, std::abs(std::cos(M_PI * p)));
            side = p < 0.5 ? actor.facing : actor.turnTo;
        }
        const QString showing = shownClip(actor, name, side);
        // without a "-left" version: the turn clip goes from as drawn to its
        // mirror, so turning back it is mirrored; any other clip is mirrored
        // facing left
        const bool mirror = !actor.views && showing == name && (turnClip ? actor.turnTo > 0 : side < 0);
        const qreal fw = sheet.frame.width(), fh = sheet.frame.height();
        // Where it is between its last two steps, kept away from what it
        // avoids, and how deep. Each figure's image has its own size where
        // it rests (the actor's depth, plus its place's); going deeper or
        // nearer along a course scales it by perspective, and its formation,
        // sway and wobble with it.
        const QPointF centre = actor.prevPos + (actor.pos - actor.prevPos) * alpha
                               + (actor.prevShift + (actor.shift - actor.prevShift) * float(alpha)).toPointF();
        const qreal zc = actor.prevZ + (actor.zNow - actor.prevZ) * alpha
                         + (actor.prevShift.z() + (actor.shift.z() - actor.prevShift.z()) * alpha);
        const qreal own = depthScale(actor.z);
        // Within the scene's depth, as each figure's own (zm): pushed in front
        // of it by what the actor avoids, zc would cross perspective's pole.
        const qreal spread = depthScale(std::clamp<qreal>(zc, 0, 1)) / own;
        for (int i = 0; i < actor.copies; ++i) {
            // the clip on show, or this copy's own in turn (still things of many looks)
            const Clip clip = !actor.copyClips.isEmpty() && !actor.turning
                                  ? sheet.clips.value(actor.copyClips.at(i % actor.copyClips.size()))
                                  : sheet.clips.value(showing, sheet.clips.value(actor.clip));
            int index;
            qreal blend = 0;     // a blend clip: how far into the next frame
            if (turnClip) {
                index = clip.first + std::clamp(int((t - actor.turnSince) * clip.fps), 0, clip.count - 1);
            } else {
                index = frameOf(actor, clip, t, i);
                if (clip.blend && clip.count > 1) {
                    const qreal f = (t - actor.clipSince + i * actor.lag) * clip.fps;
                    blend = f - std::floor(f);
                }
            }
            auto sourceOf = [&](int frame) {
                // with views, the frame is a column of the heading's row
                return actor.views ? QRectF(frame * fw, actor.view * fh, fw, fh)
                                   : QRectF((frame % sheet.columns) * fw, (frame / sheet.columns) * fh, fw, fh);
            };
            const QRectF source = sourceOf(index);
            const qreal phase = i * 0.37;
            const QVector3D offset = actor.offsets[size_t(i)];
            const qreal zm = std::clamp<qreal>(zc + offset.z(), 0, 1);
            const qreal wobble = actor.wobble.x() != 0 ? actor.wobble.x() * std::sin(kTau * t / std::max<qreal>(0.2, actor.wobble.y()) + i)
                                                       : 0;
            const qreal near = depthScale(zm) / own;      // pixels at the actor's depth to pixels here
            const QPointF at = centre + QPointF(offset.x(), offset.y() + wobble) * spread + swayAt(t, zm)
                + QPointF(state.vary.value(QStringLiteral("x"), t, phase, 0), state.vary.value(QStringLiteral("y"), t, phase, 0)) * near;
            const qreal scale = state.vary.value(QStringLiteral("scale"), t, phase, 1) * depthScale(zm)
                                / depthScale(std::clamp<qreal>(actor.z + offset.z(), 0, 1));
            const qreal opacity = state.vary.value(QStringLiteral("opacity"), t, phase, 1);
            const qreal rotation = actor.pitch + state.vary.value(QStringLiteral("rotate"), t, phase, 0);
            const qreal w = fw * k * scale * squash, h = fh * k * scale;
            if (!actor.bends) {
                const QRectF target(snap(at.x() - w / 2, fine), snap(at.y() - h / 2, fine), w, h);
                if (blend > 0) {
                    // each frame dissolving into the next
                    const int next = clip.first + (index - clip.first + 1) % clip.count;
                    list.push_back({zm, order++, DrawItem{sheet.image, source, target, opacity * (1 - blend), mirror, rotation}});
                    list.push_back({zm, order++, DrawItem{sheet.image, sourceOf(next), target, opacity * blend, mirror, rotation}});
                } else {
                    list.push_back({zm, order++, DrawItem{sheet.image, source, target, opacity, mirror, rotation}});
                }
                continue;
            }
            // Bending from its base: in bands, each moved sideways by a wave
            // rising up the stem and by the current, more the higher it is.
            const int rows = int(fh);
            const int bands = std::clamp(rows / 6, 4, 24);
            const qreal top = snap(at.y() - h / 2, fine);
            for (int j = 0; j < bands; ++j) {
                const int y0 = rows * j / bands, y1 = rows * (j + 1) / bands;
                const qreal height = 1 - (y0 + y1) * 0.5 / rows;
                const qreal sway = actor.bendAmount * std::sin(kTau * (t / actor.bendPeriod + i * 0.23 + actor.z)
                                                               - kTau * actor.bendWave * height)
                                   + actor.bendFollow * flow.x();
                const qreal dx = sway * std::pow(height, 1.5) * near;
                list.push_back({zm, order++, DrawItem{sheet.image, QRectF(source.x(), source.y() + y0, fw, y1 - y0),
                                                      QRectF(snap(at.x() - w / 2 + dx, fine), top + y0 * k * scale, w,
                                                             (y1 - y0) * k * scale),
                                                      opacity, mirror, 0}});
            }
        }
    }

    for (const Emitter &emitter : m_emitters) {
        const auto sheetIt = m_sheets.constFind(emitter.sheet);
        if (sheetIt == m_sheets.cend() || imageSize(sheetIt->image).isEmpty())
            continue;
        const Sheet &sheet = *sheetIt;
        const Clip clip = sheet.clips.value(emitter.clip);
        for (const Particle &p : emitter.particles) {
            const qreal u = p.age / std::max<qreal>(0.001, p.life);
            const int index = clip.first + (clip.count > 1 ? int(p.age * clip.fps) % clip.count : 0);
            const QRectF source((index % sheet.columns) * sheet.frame.width(), (index / sheet.columns) * sheet.frame.height(),
                                sheet.frame.width(), sheet.frame.height());
            const qreal scale = 1 + emitter.grow * u;
            const qreal w = sheet.frame.width() * k * scale, h = sheet.frame.height() * k * scale;
            const qreal opacity = std::clamp<qreal>(std::min(u / 0.1, 1.0) * (1 - emitter.fade * u), 0, 1);
            const QPointF at = p.prev + (p.pos - p.prev) * alpha + swayAt(t, emitter.z);
            list.push_back({emitter.z, order++, DrawItem{sheet.image, source,
                                                         QRectF(snap(at.x() - w / 2, fine), snap(at.y() - h / 2, fine), w, h),
                                                         opacity, false}});
        }
    }

    std::stable_sort(list.begin(), list.end(), [](const Ordered &a, const Ordered &b) {
        return a.z > b.z || (a.z == b.z && a.order < b.order);
    });
    if (list.size() > kMaxDrawItems) {
        if (!m_tooMany)
            qWarning("mun-shell: motion: %zu things in a frame; the nearest past %zu are not drawn", list.size(),
                     kMaxDrawItems);
        m_tooMany = true;
        list.resize(kMaxDrawItems);
    }
    m_items.clear();
    m_items.reserve(list.size());
    for (const Ordered &o : list)
        m_items.push_back(o.item);
}

void MotionScene::onFrame()
{
    if (!m_running || m_still || !m_images)
        return;
    ++m_frameTicks;
    tick();
}

void MotionScene::tick()
{
    if (!m_images)
        return;
    // Fixed steps of 1/rate for the time that passed since the last tick
    // (MotionClock: at most three at once; beyond that the console fell
    // behind and the steps are dropped, counted). Held still, no time
    // passes.
    const qreal step = 1.0 / m_rate;
    qreal alpha = 1;
    if (!m_still && m_clock.isValid()) {
        const MotionClock::Advance advance = m_stepper.advance(m_clock.nsecsElapsed() / 1e9);
        m_clock.restart();
        for (int i = 0; i < advance.steps; ++i) {
            m_simT += step;
            const QStringList pending = std::exchange(m_pending, {});
            for (const QString &name : pending)
                applyEvent(name, m_simT);
            const int now = int(std::lround(m_simT * m_rate));
            for (auto it = m_scheduled.begin(); it != m_scheduled.end();) {
                if (it->first <= now) {
                    applyEvent(it->second, m_simT);
                    it = m_scheduled.erase(it);
                } else {
                    ++it;
                }
            }
            simulate(m_simT, step);
            if (m_trace && std::lround(m_simT * m_rate) % m_rate == 0)
                trace();
        }
        m_lateSteps += advance.dropped;
        alpha = advance.alpha;
    }
    // Drawn between the last two steps (a step behind, at most), so motion
    // advances by the time that passed on every display frame while the
    // scene itself stays in fixed steps.
    collect(std::max<qreal>(0, m_simT - step * (1 - alpha)), alpha);
    ++m_ticks;
    ++m_totalTicks;
    if (m_backend == QLatin1String("cpu")) {
        if (m_busy.exchange(true)) {
            ++m_skipped;
        } else {
            {
                std::lock_guard<std::mutex> lock(m_mutex);
                m_jobItems = m_items;
                m_job = true;
            }
            m_wake.notify_one();
        }
    } else {
        m_itemsDirty = true;
        update();
        if (!m_drawn) {
            m_drawn = true;
            emit drawnChanged();
        }
    }
    if (m_since.isValid() && m_since.elapsed() >= 10000)
        report();
}

// ------------------------------------------------------------ the CPU backend

void MotionScene::composerLoop()
{
#ifdef __linux__
    // Named for the laboratory, which reads each thread's CPU in /proc.
    pthread_setname_np(pthread_self(), "motion-compose");
#endif
    for (;;) {
        std::vector<DrawItem> items;
        std::shared_ptr<std::vector<QImage>> images;
        QSize size;
        qreal k;
        bool nearest;
        {
            std::unique_lock<std::mutex> lock(m_mutex);
            m_wake.wait(lock, [this] { return m_stop || m_job; });
            if (m_stop)
                return;
            m_job = false;
            items.swap(m_jobItems);
            images = m_images;
            size = m_resolution;
            k = m_k;
            nearest = m_nearest;
        }
        if (!images) {
            m_busy = false;
            continue;
        }
        const auto start = std::chrono::steady_clock::now();
        QImage frame(size, QImage::Format_ARGB32_Premultiplied);
        frame.fill(Qt::transparent);
        {
            QPainter p(&frame);
            p.setRenderHint(QPainter::SmoothPixmapTransform, !nearest);
            p.scale(1 / k, 1 / k);
            for (const DrawItem &item : items) {
                if (item.image < 0 || item.image >= int(images->size()))
                    continue;
                p.setOpacity(item.opacity);
                if (item.mirror || item.rotation != 0) {
                    // About the target's centre: mirrored, then turned.
                    const QPointF centre = item.target.center();
                    p.save();
                    p.translate(centre);
                    p.rotate(item.rotation);
                    if (item.mirror)
                        p.scale(-1, 1);
                    p.translate(-centre);
                    p.drawImage(item.target, (*images)[size_t(item.image)], item.source);
                    p.restore();
                } else {
                    p.drawImage(item.target, (*images)[size_t(item.image)], item.source);
                }
            }
        }
        const qint64 ns = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - start).count();
        QPointer<MotionScene> self(this);
        QMetaObject::invokeMethod(this, [self, frame, ns] {
            if (self)
                self->composed(frame, ns);
        }, Qt::QueuedConnection);
    }
}

void MotionScene::composed(QImage frame, qint64 nanoseconds)
{
    m_frame = frame;
    m_frameDirty = true;
    m_busy = false;
    ++m_composedCount;
    m_composeNs.push_back(nanoseconds);
    update();
    if (!m_drawn) {
        m_drawn = true;
        emit drawnChanged();
    }
}

// ------------------------------------------------------------ the scene graph

QSGNode *MotionScene::updatePaintNode(QSGNode *old, UpdatePaintNodeData *)
{
    ++m_paints;
    if (!m_images || !window()) {
        delete old;
        return nullptr;
    }
    const auto filtering = m_nearest ? QSGTexture::Nearest : QSGTexture::Linear;
    if (m_backend == QLatin1String("cpu")) {
        auto *root = static_cast<FrameRoot *>(old);
        if (!root)
            root = new FrameRoot;
        if (m_frameDirty && !m_frame.isNull()) {
            QSGTexture *texture = window()->createTextureFromImage(m_frame);
            if (root->node)
                root->node->setTexture(texture);
            delete root->texture;
            root->texture = texture;
            m_frameDirty = false;
        }
        if (!root->texture)
            return root;
        // A node joins the tree only with its texture: Qt Quick's renderers
        // visit it as soon as it is added.
        const bool fresh = !root->node;
        if (fresh) {
            root->node = window()->createImageNode();
            root->node->setOwnsTexture(false);
            root->node->setTexture(root->texture);
        }
        root->node->setFiltering(filtering);
        root->node->setRect(QRectF(0, 0, width(), height()));
        root->node->setSourceRect(QRectF(QPointF(), root->texture->textureSize()));
        if (fresh)
            root->appendChildNode(root->node);
        return root;
    }

    if (window()->rendererInterface()->graphicsApi() != QSGRendererInterface::Software)
        return updateBatches(old);

    auto *root = static_cast<GraphRoot *>(old);
    if (root && root->source != m_images) {
        delete root;
        root = nullptr;
    }
    if (!root) {
        root = new GraphRoot;
        root->source = m_images;
    }
    if (!m_itemsDirty)
        return root;
    m_itemsDirty = false;
    // One node (under an opacity node and a transform) per thing drawn, kept between frames;
    // the pool grows with the list and hides what it does not use. A new
    // node is set up before it joins the tree: Qt Quick's renderers visit a
    // node as soon as it is added, and one without a texture is not drawable.
    const size_t count = std::max(root->nodes.size(), m_items.size());
    for (size_t i = 0; i < count; ++i) {
        const bool used = i < m_items.size() && m_items[i].image >= 0 && m_items[i].image < int(m_images->size());
        const bool fresh = i >= root->nodes.size();
        if (fresh && !used)
            break;     // nothing to draw from here on (collect lists drawable things only)
        QSGOpacityNode *fade = fresh ? new QSGOpacityNode : root->fades[i];
        QSGTransformNode *turn = fresh ? new QSGTransformNode : root->turns[i];
        QSGImageNode *node = fresh ? window()->createImageNode() : root->nodes[i];
        if (fresh)
            node->setOwnsTexture(false);    // the textures are the root's, shared
        if (!used) {
            fade->setOpacity(0);
            continue;
        }
        const DrawItem &item = m_items[i];
        QSGTexture *&texture = root->textures[item.image];
        if (!texture)
            texture = window()->createTextureFromImage((*m_images)[size_t(item.image)]);
        if (node->texture() != texture)
            node->setTexture(texture);
        node->setFiltering(filtering);
        node->setSourceRect(item.source);
        node->setRect(item.target);
        node->setTextureCoordinatesTransform(item.mirror ? QSGImageNode::MirrorHorizontally : QSGImageNode::NoTransform);
        fade->setOpacity(item.opacity);
        // Turned about the target's centre (mirrored within it first, as
        // the composer does); untouched while nothing turns.
        const qreal held = fresh ? 0 : root->angles[i];
        if (item.rotation != 0 || held != 0) {
            QMatrix4x4 m;
            if (item.rotation != 0) {
                const QPointF centre = item.target.center();
                m.translate(float(centre.x()), float(centre.y()));
                m.rotate(float(item.rotation), 0, 0, 1);
                m.translate(float(-centre.x()), float(-centre.y()));
            }
            turn->setMatrix(m);
        }
        if (fresh) {
            turn->appendChildNode(node);
            fade->appendChildNode(turn);
            root->appendChildNode(fade);
            root->fades.push_back(fade);
            root->turns.push_back(turn);
            root->angles.push_back(item.rotation);
            root->nodes.push_back(node);
        } else {
            root->angles[i] = item.rotation;
        }
    }
    return root;
}

// The graph backend on the graphics path: runs of things from one image as
// one node each (SpriteMaterial). Qt Quick's software renderer, which has no
// shaders, keeps a node per thing (updatePaintNode).
QSGNode *MotionScene::updateBatches(QSGNode *old)
{
    auto *root = static_cast<BatchRoot *>(old);
    if (root && root->source != m_images) {
        delete root;
        root = nullptr;
    }
    if (!root) {
        root = new BatchRoot;
        root->source = m_images;
    }
    if (!m_itemsDirty)
        return root;
    m_itemsDirty = false;
    const auto filtering = m_nearest ? QSGTexture::Nearest : QSGTexture::Linear;
    size_t run = 0;
    for (size_t i = 0; i < m_items.size();) {
        const int image = m_items[i].image;
        size_t end = i + 1;
        while (end < m_items.size() && m_items[end].image == image && end - i < kMaxRunQuads)
            ++end;
        if (image < 0 || image >= int(m_images->size())) {
            i = end;
            continue;
        }
        QSGTexture *&texture = root->textures[image];
        if (!texture) {
            texture = window()->createTextureFromImage((*m_images)[size_t(image)]);
            texture->setFiltering(filtering);
            texture->setMipmapFiltering(QSGTexture::None);
        }
        // A node joins the tree with its material and geometry set up:
        // Qt Quick's renderers visit it as soon as it is added.
        const bool fresh = run >= root->runs.size();
        QSGGeometryNode *node = fresh ? new QSGGeometryNode : root->runs[run];
        auto *material = static_cast<SpriteMaterial *>(node->material());
        if (fresh) {
            material = new SpriteMaterial;
            node->setMaterial(material);
            node->setGeometry(new QSGGeometry(spriteAttributes(), 0, 0, QSGGeometry::UnsignedShortType));
            node->geometry()->setDrawingMode(QSGGeometry::DrawTriangles);
            node->setFlags(QSGNode::OwnsGeometry | QSGNode::OwnsMaterial);
        }
        if (material->texture != texture) {
            material->texture = texture;
            node->markDirty(QSGNode::DirtyMaterial);
        }
        const int quads = int(end - i);
        QSGGeometry *geometry = node->geometry();
        if (geometry->vertexCount() != quads * 4)
            geometry->allocate(quads * 4, quads * 6);
        auto *vertex = static_cast<SpriteVertex *>(geometry->vertexData());
        quint16 *index = geometry->indexDataAsUShort();
        const QSizeF size = texture->textureSize();
        const QRectF sub = texture->normalizedTextureSubRect();
        for (int q = 0; q < quads; ++q) {
            const DrawItem &item = m_items[i + size_t(q)];
            float u0 = float(sub.x() + item.source.left() / size.width() * sub.width());
            float u1 = float(sub.x() + item.source.right() / size.width() * sub.width());
            const float v0 = float(sub.y() + item.source.top() / size.height() * sub.height());
            const float v1 = float(sub.y() + item.source.bottom() / size.height() * sub.height());
            if (item.mirror)
                std::swap(u0, u1);
            // corners, turned about the target's centre
            const QPointF c = item.target.center();
            const qreal hw = item.target.width() / 2, hh = item.target.height() / 2;
            const qreal a = item.rotation * M_PI / 180, cs = std::cos(a), sn = std::sin(a);
            auto corner = [&](qreal dx, qreal dy, float u, float v) {
                return SpriteVertex{float(c.x() + dx * cs - dy * sn), float(c.y() + dx * sn + dy * cs), u, v, float(item.opacity)};
            };
            vertex[q * 4 + 0] = corner(-hw, -hh, u0, v0);
            vertex[q * 4 + 1] = corner(hw, -hh, u1, v0);
            vertex[q * 4 + 2] = corner(hw, hh, u1, v1);
            vertex[q * 4 + 3] = corner(-hw, hh, u0, v1);
            const quint16 b = quint16(q * 4);
            const quint16 corners[6] = {b, quint16(b + 1), quint16(b + 2), b, quint16(b + 2), quint16(b + 3)};
            std::memcpy(index + q * 6, corners, sizeof corners);
        }
        node->markDirty(QSGNode::DirtyGeometry);
        if (fresh) {
            root->appendChildNode(node);
            root->runs.push_back(node);
        }
        ++run;
        i = end;
    }
    m_runs = int(run);
    // the runs this frame does not use draw nothing
    for (size_t r = run; r < root->runs.size(); ++r) {
        QSGGeometry *geometry = root->runs[r]->geometry();
        if (geometry->vertexCount() != 0) {
            geometry->allocate(0, 0);
            root->runs[r]->markDirty(QSGNode::DirtyGeometry);
        }
    }
    return root;
}

// Laboratory (MUN_SHELL_MOTION_TRACE): once a second of scene time, a digest
// of what the simulation decided (every actor's place, depth, heading,
// facing, state and push; every particle; every light's opacity), the same
// on any console given the same events at the same steps, for comparing
// two machines' runs step by step. Drawing, which follows the display's
// frames, is not part of it.
void MotionScene::trace()
{
    quint64 hash = 1469598103934665603ull;
    auto mix = [&hash](qint64 value) {
        for (int i = 0; i < 8; ++i) {
            hash ^= quint64(value >> (8 * i)) & 0xff;
            hash *= 1099511628211ull;
        }
    };
    // names by their characters (qHash is seeded differently in each process)
    auto text = [&mix](const QString &value) {
        for (const QChar c : value)
            mix(c.unicode());
        mix(-1);
    };
    auto fixed = [](qreal value, qreal scale) { return qint64(std::llround(value * scale)); };
    int moving = 0, particles = 0;
    for (const Actor &actor : m_actors) {
        text(actor.name);
        text(actor.state);
        mix(fixed(actor.pos.x(), 10));
        mix(fixed(actor.pos.y(), 10));
        mix(fixed(actor.zNow, 1000));
        mix(fixed(actor.shift.x(), 10));
        mix(fixed(actor.shift.y(), 10));
        mix(actor.view);
        mix(actor.facing);
        mix(actor.hidden);
        moving += !actor.course.empty();
    }
    for (const Emitter &emitter : m_emitters) {
        mix(qint64(emitter.particles.size()));
        for (const Particle &p : emitter.particles) {
            mix(fixed(p.pos.x(), 10));
            mix(fixed(p.pos.y(), 10));
        }
        particles += int(emitter.particles.size());
    }
    for (const Light &light : m_lights)
        mix(fixed(light.opacity, 1000));
    qInfo("mun-shell: motion: trace step %ld %016llx, %d actors on a course, %d particles",
          long(std::lround(m_simT * m_rate)), static_cast<unsigned long long>(hash), moving, particles);
}

void MotionScene::report()
{
    std::vector<qint64> ns;
    ns.swap(m_composeNs);
    std::sort(ns.begin(), ns.end());
    const auto at = [&ns](double q) { return ns.empty() ? 0.0 : ns[std::min(ns.size() - 1, size_t(q * ns.size()))] / 1e6; };
    qInfo("mun-shell: motion: %s, %d ticks, %d composed, %d skipped, compose p50 %.1f ms p95 %.1f ms, %d things drawn, "
          "%d steps late in all, at %.2f s; %d ticks on the display's frames, %d paints",
          qPrintable(m_backend), m_ticks, m_composedCount, m_skipped, at(0.5), at(0.95), int(m_items.size()),
          m_lateSteps, m_simT, m_frameTicks, m_paints);
    m_frameTicks = m_paints = 0;
    m_ticks = m_composedCount = m_skipped = 0;
    m_since.restart();
}
