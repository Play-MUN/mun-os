#include "orbitalbackdrop.h"

#include "sceneclock.h"

#include <QConicalGradient>
#include <QLinearGradient>
#include <QMatrix4x4>
#include <QPainter>
#include <QPainterPath>
#include <QQuickWindow>
#include <QRadialGradient>
#include <QSGGeometryNode>
#include <QSGImageNode>
#include <QSGMaterial>
#include <QSGMaterialShader>
#include <QSGTransformNode>
#include <QSGVertexColorMaterial>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>

namespace {

constexpr int kRate = 10;           // frames per second of the motion (SceneClock)
constexpr qreal kPi = 3.14159265358979;
constexpr qreal kWidth = 1920;
constexpr qreal kHeight = 1080;
const QPointF kCentre(OrbitalBackdrop::kCentreX, OrbitalBackdrop::kCentreY);
// The orbit's texture: the rings, grid and axes out to 30 px beyond the outer
// ring. The dots': their ring and a margin.
constexpr qreal kOrbitHalf = OrbitalBackdrop::kOuterRing + 30;
constexpr qreal kDotsHalf = 180;
// The eclipse's quad: the upper left corner its light reaches.
const QRectF kEclipseRect(0, 0, 560, 590);
// The waves' mesh: a column every this many pixels, and the strip around
// each edge where its light is computed.
constexpr qreal kColumn = 10;
constexpr float kStrip = 36;
constexpr float kBottom = 1e4f;     // a fill vertex at the bottom of the screen

qreal mix(qreal a, qreal b, qreal t) { return a + (b - a) * t; }

// How much of the eclipse's light the edge gets at a screen angle (degrees,
// clockwise from three o'clock): as shaders/eclipse.frag.
qreal eclipseLight(qreal deg)
{
    const qreal t = (deg - 182) / 86;
    if (t <= 0 || t >= 1)
        return 0;
    return std::pow(std::sin(kPi * std::pow(t, 0.7)), 1.8);
}

// The waves at rest, as shaders/wave.vert places them at time 0.
qreal waveAt(qreal x, bool front)
{
    const qreal xs[4] = {front ? 0.0 : 700.0, front ? 494.0 : 1150.0, front ? 1148.0 : 1500.0, 1920};
    const qreal ys[4] = {front ? 792.0 : 900.0, front ? 893.0 : 805.0, front ? 817.0 : 795.0, front ? 655.0 : 610.0};
    const int i = x <= xs[1] ? 0 : x <= xs[2] ? 1 : 2;
    const qreal u = std::clamp((x - xs[i]) / (xs[i + 1] - xs[i]), 0.0, 1.0);
    return mix(ys[i], ys[i + 1], 0.5 - 0.5 * std::cos(kPi * u));
}

// The night: darker downwards, with the eclipse's warm haze in its upper
// left. Both paths take their colours from here.
QColor nightAt(qreal x, qreal y)
{
    const qreal t = y / kHeight;
    const QColor top(0x17, 0x12, 0x0e), middle(0x12, 0x0e, 0x0b), low(0x0f, 0x0c, 0x0a);
    const QColor &a = t < 0.55 ? top : middle;
    const QColor &b = t < 0.55 ? middle : low;
    const qreal u = t < 0.55 ? t / 0.55 : (t - 0.55) / 0.45;
    qreal r = mix(a.redF(), b.redF(), u), g = mix(a.greenF(), b.greenF(), u), bl = mix(a.blueF(), b.blueF(), u);
    const qreal haze = 0.07 * std::max(0.0, 1 - std::hypot(x - (kCentre.x() - 230), y - (kCentre.y() - 260)) / 520);
    r = mix(r, 233 / 255.0, haze);
    g = mix(g, 176 / 255.0, haze);
    bl = mix(bl, 133 / 255.0, haze);
    return QColor::fromRgbF(float(r), float(g), float(bl));
}

QColor white(qreal alpha) { return QColor::fromRgbF(1, 1, 1, float(alpha)); }

// The orbit, in the item's coordinates: the outer part (grid, axes, the two
// outer rings and their nodes) faded out before the menu, then the inner
// rings and their nodes.
void paintOrbit(QPainter &p)
{
    p.setRenderHint(QPainter::Antialiasing);
    const QPointF c = kCentre;
    const auto circle = [&](qreal r, const QColor &colour, qreal width) {
        p.setPen(QPen(colour, width));
        p.setBrush(Qt::NoBrush);
        p.drawEllipse(c, r, r);
    };
    const auto node = [&](qreal r, qreal deg) {
        p.setPen(Qt::NoPen);
        p.setBrush(QColor::fromRgbF(0.957f, 0.925f, 0.878f, 0.9f));
        p.drawEllipse(c + QPointF(r * std::cos(deg * kPi / 180), r * std::sin(deg * kPi / 180)), 3.4, 3.4);
    };
    const qreal outer = OrbitalBackdrop::kOuterRing;
    // The outer part, on a layer of its own so that it can be faded.
    QImage layer(p.device()->width(), p.device()->height(), QImage::Format_ARGB32_Premultiplied);
    layer.setDevicePixelRatio(p.device()->devicePixelRatioF());
    layer.fill(Qt::transparent);
    {
        QPainter q(&layer);
        q.setTransform(p.transform());
        q.setRenderHint(QPainter::Antialiasing);
        q.setPen(QPen(white(0.035), 1));
        for (int a = 0; a < 360; a += 30) {
            const qreal r = a * kPi / 180;
            q.drawLine(c + QPointF(OrbitalBackdrop::kRadii[0] * std::cos(r), OrbitalBackdrop::kRadii[0] * std::sin(r)),
                       c + QPointF((outer + 30) * std::cos(r), (outer + 30) * std::sin(r)));
        }
        q.setPen(QPen(white(0.045), 1));
        q.drawEllipse(c, OrbitalBackdrop::kRadii[5], OrbitalBackdrop::kRadii[5]);
        q.setPen(QPen(white(0.07), 1));
        q.drawLine(c + QPointF(-outer - 30, 0), c + QPointF(outer + 30, 0));
        q.drawLine(c + QPointF(0, -outer - 30), c + QPointF(0, outer + 30));
        q.setPen(QPen(white(0.08), 1));
        q.drawEllipse(c, outer, outer);
        q.setPen(Qt::NoPen);
        q.setBrush(QColor::fromRgbF(0.957f, 0.925f, 0.878f, 0.9f));
        for (const qreal deg : {90.0, 180.0, 270.0})
            q.drawEllipse(c + QPointF(outer * std::cos(deg * kPi / 180), outer * std::sin(deg * kPi / 180)), 3.4, 3.4);
        // Faded out from 110 px before the menu to 40 px before it.
        QLinearGradient fade(OrbitalBackdrop::kMenuX - 110, 0, OrbitalBackdrop::kMenuX - 40, 0);
        fade.setColorAt(0, QColor(0, 0, 0, 255));
        fade.setColorAt(1, QColor(0, 0, 0, 0));
        q.setCompositionMode(QPainter::CompositionMode_DestinationIn);
        q.fillRect(QRectF(c.x() - kOrbitHalf, c.y() - kOrbitHalf, 2 * kOrbitHalf, 2 * kOrbitHalf), fade);
    }
    p.save();
    p.resetTransform();
    p.drawImage(0, 0, layer);
    p.restore();
    // The inner part.
    circle(OrbitalBackdrop::kNodeRing, white(0.08), 1);
    circle(OrbitalBackdrop::kRadii[3], white(0.06), 1);
    circle(OrbitalBackdrop::kRadii[2], QColor::fromRgbF(0.925f, 0.839f, 0.753f, 0.42f), 1.3);
    circle(OrbitalBackdrop::kRadii[0], white(0.10), 1);
    for (const qreal deg : {0.0, 90.0, 180.0, 270.0})
        node(OrbitalBackdrop::kRadii[2], deg);
}

// The ring of dots, one every 3 degrees.
void paintDots(QPainter &p)
{
    p.setRenderHint(QPainter::Antialiasing);
    p.setPen(Qt::NoPen);
    p.setBrush(white(0.28));
    const qreal r = OrbitalBackdrop::kRadii[1];
    for (int a = 0; a < 360; a += 3)
        p.drawEllipse(kCentre + QPointF(r * std::cos(a * kPi / 180), r * std::sin(a * kPi / 180)), 1.1, 1.1);
}

// The software path's still picture: everything, the waves at rest.
void paintStill(QImage &image)
{
    QPainter p(&image);
    p.setRenderHint(QPainter::Antialiasing);
    // The night, row by row (the haze varies across, slowly).
    for (int y = 0; y < int(kHeight); y += 4) {
        QLinearGradient row(0, 0, kWidth, 0);
        for (int x = 0; x <= int(kWidth); x += 240)
            row.setColorAt(x / kWidth, nightAt(x, y + 2));
        p.fillRect(QRectF(0, y, kWidth, 4), row);
    }
    // The eclipse: its halo and edge on a layer, masked by the light each
    // angle gets, added.
    {
        QImage layer(image.size(), QImage::Format_ARGB32_Premultiplied);
        layer.setDevicePixelRatio(image.devicePixelRatioF());
        layer.fill(Qt::transparent);
        QPainter q(&layer);
        q.setRenderHint(QPainter::Antialiasing);
        const qreal r = OrbitalBackdrop::kOuterRing;
        QRadialGradient halo(kCentre, r + 90);
        const auto at = [&](qreal d) { return std::clamp((r + d) / (r + 90), 0.0, 1.0); };
        halo.setColorAt(at(-24), QColor::fromRgbF(0.92f, 0.62f, 0.40f, 0));
        halo.setColorAt(at(-4), QColor::fromRgbF(0.92f, 0.62f, 0.40f, 0.35f));
        halo.setColorAt(at(0), QColor::fromRgbF(0.98f, 0.78f, 0.58f, 0.75f));
        halo.setColorAt(at(10), QColor::fromRgbF(0.94f, 0.65f, 0.43f, 0.35f));
        halo.setColorAt(at(40), QColor::fromRgbF(0.90f, 0.58f, 0.38f, 0.10f));
        halo.setColorAt(1, QColor::fromRgbF(0.90f, 0.58f, 0.38f, 0));
        q.setPen(QPen(QBrush(halo), 114));
        q.drawEllipse(kCentre, r + 33, r + 33);
        q.setPen(QPen(QColor(255, 232, 204), 3));
        q.drawEllipse(kCentre, r + 0.5, r + 0.5);
        // Qt's conical gradient turns anticlockwise from three o'clock: at
        // position p the screen's clockwise angle is 360 (1 - p).
        QConicalGradient mask(kCentre, 0);
        for (int i = 0; i <= 72; ++i) {
            const qreal pos = i / 72.0;
            mask.setColorAt(pos, QColor::fromRgbF(0, 0, 0, float(eclipseLight(std::fmod(360 - pos * 360, 360)))));
        }
        q.setCompositionMode(QPainter::CompositionMode_DestinationIn);
        q.fillRect(kEclipseRect, mask);
        q.setCompositionMode(QPainter::CompositionMode_Clear);
        q.fillRect(QRectF(kEclipseRect.right(), 0, kWidth, kHeight), Qt::transparent);
        q.fillRect(QRectF(0, kEclipseRect.bottom(), kWidth, kHeight), Qt::transparent);
        q.end();
        p.save();
        p.setCompositionMode(QPainter::CompositionMode_Plus);
        p.resetTransform();
        p.drawImage(0, 0, layer);
        p.restore();
    }
    paintOrbit(p);
    paintDots(p);
    // The waves at rest: fills, then their edges added.
    for (const bool front : {false, true}) {
        QPainterPath edge, fill;
        const qreal from = front ? 0 : 700;
        for (qreal x = from; x <= kWidth; x += 4) {
            const QPointF point(x, waveAt(x, front));
            if (x == from) {
                edge.moveTo(point);
                fill.moveTo(x, kHeight);
                fill.lineTo(point);
            } else {
                edge.lineTo(point);
                fill.lineTo(point);
            }
        }
        fill.lineTo(kWidth, kHeight);
        fill.closeSubpath();
        QLinearGradient shade(0, front ? 780 : 600, 0, kHeight);
        shade.setColorAt(0, front ? QColor::fromRgbF(0.149f, 0.129f, 0.110f) : QColor::fromRgbF(0.129f, 0.114f, 0.098f, 0.85f));
        shade.setColorAt(1, front ? QColor::fromRgbF(0.059f, 0.047f, 0.039f) : QColor::fromRgbF(0.067f, 0.055f, 0.047f, 0.85f));
        p.setPen(Qt::NoPen);
        p.setBrush(shade);
        p.drawPath(fill);
        QLinearGradient light(0, 0, kWidth, 0);
        const auto rim = [&](qreal x) {
            if (!front)
                return 0.35 * std::clamp((x - 700) / 200, 0.0, 1.0);
            const qreal left = std::exp(-std::pow((x - 170) / 230, 2));
            const qreal tail = 0.30 * std::exp(-std::pow((x - 560) / 460, 2));
            const qreal right = 0.85 * std::clamp((x - 1350) / 530, 0.0, 1.0);
            return std::min(1.0, 0.30 + 1.25 * left + tail + right);
        };
        for (int x = 0; x <= int(kWidth); x += 120)
            light.setColorAt(x / kWidth, QColor::fromRgbF(1.0f, 0.886f, 0.745f, float(rim(x))));
        p.setCompositionMode(QPainter::CompositionMode_Plus);
        p.setBrush(Qt::NoBrush);
        for (const auto &[width, alpha] : {std::pair{24.0, 0.10}, {8.0, 0.22}, {1.8, 1.0}}) {
            p.setOpacity(alpha);
            p.setPen(QPen(QBrush(light), width, Qt::SolidLine, Qt::RoundCap));
            p.drawPath(edge);
        }
        p.setOpacity(1);
        p.setCompositionMode(QPainter::CompositionMode_SourceOver);
    }
}

// --------------------------------------------------------------- GPU path

// The two shaders' uniforms after the matrix (std140: vec4s).
struct Uniforms {
    float view[4];    // opacity, time, breath or motion, one device pixel
    float shape[4];   // eclipse: centre x, y, radius; waves: the bottom
};
constexpr int kMatrixBytes = 64;

template<int Tag>
class LayerMaterial : public QSGMaterial {
public:
    LayerMaterial(const char *vertex, const char *fragment) : m_vertex(vertex), m_fragment(fragment)
    {
        // Blended (premultiplied: the light comes with alpha 0 and adds), and
        // never merged: the vertices are not all positions.
        setFlag(Blending);
        setFlag(RequiresFullMatrix);
    }
    QSGMaterialType *type() const override
    {
        static QSGMaterialType type;
        return &type;
    }
    QSGMaterialShader *createShader(QSGRendererInterface::RenderMode) const override
    {
        return new Shader(m_vertex, m_fragment);
    }

    Uniforms uniforms{};

private:
    class Shader : public QSGMaterialShader {
    public:
        Shader(const char *vertex, const char *fragment)
        {
            setShaderFileName(VertexStage, QString::fromLatin1(vertex));
            setShaderFileName(FragmentStage, QString::fromLatin1(fragment));
        }
        bool updateUniformData(RenderState &state, QSGMaterial *newMaterial, QSGMaterial *) override
        {
            QByteArray *buffer = state.uniformData();
            char *data = buffer->data();
            bool changed = false;
            if (state.isMatrixDirty()) {
                const QMatrix4x4 matrix = state.combinedMatrix();
                std::memcpy(data, matrix.constData(), kMatrixBytes);
                changed = true;
            }
            Uniforms uniforms = static_cast<LayerMaterial *>(newMaterial)->uniforms;
            uniforms.view[0] = state.opacity();
            if (std::memcmp(data + kMatrixBytes, &uniforms, sizeof(Uniforms)) != 0) {
                std::memcpy(data + kMatrixBytes, &uniforms, sizeof(Uniforms));
                changed = true;
            }
            return changed;
        }
    };
    const char *m_vertex;
    const char *m_fragment;
};
using EclipseMaterial = LayerMaterial<0>;
using WaveMaterial = LayerMaterial<1>;

// The night: a grid with a colour at each vertex (120 px apart: the haze is
// smooth at that spacing).
QSGGeometryNode *nightNode()
{
    constexpr int cols = 17, rows = 10;
    auto *geometry = new QSGGeometry(QSGGeometry::defaultAttributes_ColoredPoint2D(), cols * rows, (cols - 1) * (rows - 1) * 6);
    geometry->setDrawingMode(QSGGeometry::DrawTriangles);
    auto *v = geometry->vertexDataAsColoredPoint2D();
    for (int j = 0; j < rows; ++j) {
        for (int i = 0; i < cols; ++i) {
            const qreal x = kWidth * i / (cols - 1), y = kHeight * j / (rows - 1);
            const QColor c = nightAt(x, y);
            v[j * cols + i].set(float(x), float(y), uchar(c.red()), uchar(c.green()), uchar(c.blue()), 255);
        }
    }
    quint16 *index = geometry->indexDataAsUShort();
    for (int j = 0; j + 1 < rows; ++j) {
        for (int i = 0; i + 1 < cols; ++i) {
            const quint16 a = quint16(j * cols + i), b = quint16(a + 1), c = quint16(a + cols), d = quint16(c + 1);
            for (const quint16 k : {a, b, c, c, b, d})
                *index++ = k;
        }
    }
    auto *node = new QSGGeometryNode;
    node->setGeometry(geometry);
    node->setMaterial(new QSGVertexColorMaterial);
    node->setFlags(QSGNode::OwnsGeometry | QSGNode::OwnsMaterial);
    return node;
}

QSGGeometryNode *eclipseNode()
{
    auto *geometry = new QSGGeometry(QSGGeometry::defaultAttributes_Point2D(), 4);
    geometry->setDrawingMode(QSGGeometry::DrawTriangleStrip);
    QSGGeometry::updateRectGeometry(geometry, kEclipseRect);
    auto *material = new EclipseMaterial(":/shaders/eclipse.vert.qsb", ":/shaders/eclipse.frag.qsb");
    material->uniforms.shape[0] = float(kCentre.x());
    material->uniforms.shape[1] = float(kCentre.y());
    material->uniforms.shape[2] = float(OrbitalBackdrop::kOuterRing);
    auto *node = new QSGGeometryNode;
    node->setGeometry(geometry);
    node->setMaterial(material);
    node->setFlags(QSGNode::OwnsGeometry | QSGNode::OwnsMaterial);
    return node;
}

// The waves' mesh: for each wave, its fill (a vertex on the curve and one at
// the bottom, per column) and its edge's strip (one above and one below the
// curve), drawn back wave first, each fill before its edge.
struct WaveVertex {
    float x, y;                  // at rest (the renderer's bounds)
    float offset, wave, part, unused;
};

const QSGGeometry::AttributeSet &waveAttributes()
{
    static const QSGGeometry::Attribute list[] = {
        QSGGeometry::Attribute::createWithAttributeType(0, 2, QSGGeometry::FloatType, QSGGeometry::PositionAttribute),
        QSGGeometry::Attribute::createWithAttributeType(1, 4, QSGGeometry::FloatType, QSGGeometry::UnknownAttribute),
    };
    static const QSGGeometry::AttributeSet set = {2, sizeof(WaveVertex), list};
    return set;
}

QSGGeometryNode *wavesNode()
{
    std::vector<WaveVertex> vertices;
    std::vector<quint16> indices;
    const auto strip = [&](bool front, bool edge) {
        const qreal from = front ? 0 : 700;
        const auto base = quint16(vertices.size());
        int columns = 0;
        for (qreal x = from; x <= kWidth + 0.5; x += kColumn, ++columns) {
            const float rest = float(waveAt(x, front));
            const float a = edge ? -kStrip : 0, b = edge ? kStrip : kBottom;
            vertices.push_back({float(x), rest + a, a, front ? 1.f : 0.f, edge ? 1.f : 0.f, 0});
            vertices.push_back({float(x), edge ? rest + b : float(kHeight), b, front ? 1.f : 0.f, edge ? 1.f : 0.f, 0});
        }
        for (int i = 0; i + 1 < columns; ++i) {
            const quint16 a = quint16(base + 2 * i), b = quint16(a + 1), c = quint16(a + 2), d = quint16(a + 3);
            for (const quint16 k : {a, b, c, c, b, d})
                indices.push_back(k);
        }
    };
    strip(false, false);
    strip(false, true);
    strip(true, false);
    strip(true, true);
    auto *geometry = new QSGGeometry(waveAttributes(), int(vertices.size()), int(indices.size()), QSGGeometry::UnsignedShortType);
    geometry->setDrawingMode(QSGGeometry::DrawTriangles);
    std::memcpy(geometry->vertexData(), vertices.data(), vertices.size() * sizeof(WaveVertex));
    std::memcpy(geometry->indexData(), indices.data(), indices.size() * sizeof(quint16));
    auto *material = new WaveMaterial(":/shaders/wave.vert.qsb", ":/shaders/wave.frag.qsb");
    material->uniforms.shape[0] = float(kHeight);
    auto *node = new QSGGeometryNode;
    node->setGeometry(geometry);
    node->setMaterial(material);
    node->setFlags(QSGNode::OwnsGeometry | QSGNode::OwnsMaterial);
    return node;
}

// The GPU path's tree, its layers in order.
class Layers : public QSGNode {
public:
    QSGGeometryNode *night = nullptr;
    QSGGeometryNode *eclipse = nullptr;
    QSGImageNode *orbit = nullptr;
    QSGTransformNode *turn = nullptr;
    QSGImageNode *dots = nullptr;
    QSGGeometryNode *waves = nullptr;
    qreal scale = 0;
};

// A texture of `rect` (item units) at `scale`, painted by `paint`.
QSGImageNode *paintedNode(QQuickWindow *window, const QRectF &rect, qreal scale, void (*paint)(QPainter &))
{
    QImage image(QSize(int(std::ceil(rect.width() * scale)), int(std::ceil(rect.height() * scale))),
                 QImage::Format_ARGB32_Premultiplied);
    image.setDevicePixelRatio(scale);
    image.fill(Qt::transparent);
    {
        QPainter p(&image);
        p.translate(-rect.topLeft());
        paint(p);
    }
    auto *node = window->createImageNode();
    node->setOwnsTexture(true);
    // The image is let go here: the texture has its pixels.
    node->setTexture(window->createTextureFromImage(image));
    node->setRect(rect);
    node->setSourceRect(QRectF(QPointF(0, 0), node->texture()->textureSize()));
    return node;
}

} // namespace

OrbitalBackdrop::OrbitalBackdrop(QQuickItem *parent) : QQuickItem(parent)
{
    setFlag(ItemHasContents);
    connect(SceneClock::instance(), &SceneClock::tick, this, &OrbitalBackdrop::onTick);
    connect(SceneClock::instance(), &SceneClock::syncedChanged, this, &OrbitalBackdrop::updateDemand);
    updateDemand();
}

OrbitalBackdrop::~OrbitalBackdrop()
{
    SceneClock::instance()->demand(this, 0);
}

void OrbitalBackdrop::setRunning(bool running)
{
    if (running == m_running)
        return;
    m_running = running;
    m_last = -1;
    updateDemand();
    emit runningChanged();
}

void OrbitalBackdrop::geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry)
{
    QQuickItem::geometryChange(newGeometry, oldGeometry);
    if (newGeometry.size() != oldGeometry.size()) {
        m_reset = true;
        update();
    }
}

void OrbitalBackdrop::updateDemand()
{
    // Motion only where it is cheap: on an accelerated renderer (a synced
    // clock). The software renderer shows the still picture.
    const bool moving = m_running && SceneClock::instance()->synced();
    if (!moving)
        m_last = -1;
    SceneClock::instance()->demand(this, moving ? kRate : 0);
}

void OrbitalBackdrop::onTick(quint64 frame)
{
    if (!m_running || !SceneClock::instance()->synced() || !SceneClock::due(frame, kRate))
        return;
    const qreal now = SceneClock::instance()->seconds();
    if (m_last >= 0)
        m_time += now - m_last;
    m_last = now;
    update();
}

void OrbitalBackdrop::setDial(qreal dial)
{
    if (qFuzzyCompare(dial, m_dial))
        return;
    m_dial = dial;
    update();
    emit dialChanged();
}

QSGNode *OrbitalBackdrop::updatePaintNode(QSGNode *old, UpdatePaintNodeData *)
{
    if (width() <= 0 || height() <= 0 || !window()) {
        delete old;
        return nullptr;
    }
    const qreal scale = window()->effectiveDevicePixelRatio();
    const bool rhi = QSGRendererInterface::isApiRhiBased(window()->rendererInterface()->graphicsApi());

    if (!rhi) {
        // One still picture, painted again only for a new size or scale.
        auto *node = static_cast<QSGImageNode *>(old);
        const QSize deviceSize(int(std::lround(kWidth * scale)), int(std::lround(kHeight * scale)));
        if (node && (m_reset || node->texture()->textureSize() != deviceSize)) {
            delete node;
            node = nullptr;
        }
        if (!node) {
            QImage image(deviceSize, QImage::Format_RGB32);
            image.setDevicePixelRatio(scale);
            paintStill(image);
            node = window()->createImageNode();
            node->setOwnsTexture(true);
            node->setTexture(window()->createTextureFromImage(image));
            node->setSourceRect(QRectF(QPointF(0, 0), node->texture()->textureSize()));
        }
        node->setRect(QRectF(0, 0, kWidth, kHeight));
        m_reset = false;
        return node;
    }

    auto *layers = static_cast<Layers *>(old);
    if (layers && (m_reset || !qFuzzyCompare(layers->scale, scale))) {
        delete layers;
        layers = nullptr;
    }
    if (!layers) {
        layers = new Layers;
        layers->scale = scale;
        layers->night = nightNode();
        layers->eclipse = eclipseNode();
        layers->orbit = paintedNode(window(), QRectF(kCentre.x() - kOrbitHalf, kCentre.y() - kOrbitHalf, 2 * kOrbitHalf, 2 * kOrbitHalf),
                                    scale, paintOrbit);
        layers->turn = new QSGTransformNode;
        layers->dots = paintedNode(window(), QRectF(kCentre.x() - kDotsHalf, kCentre.y() - kDotsHalf, 2 * kDotsHalf, 2 * kDotsHalf),
                                   scale, paintDots);
        layers->waves = wavesNode();
        layers->appendChildNode(layers->night);
        layers->appendChildNode(layers->eclipse);
        layers->appendChildNode(layers->orbit);
        layers->appendChildNode(layers->turn);
        layers->turn->appendChildNode(layers->dots);
        layers->appendChildNode(layers->waves);
        layers->setFlag(QSGNode::OwnedByParent);
        m_reset = false;
    }
    // The moment's numbers: the eclipse breathes over 14 s, the dots are
    // turned as the dial says, the waves take the time.
    const qreal breath = 0.91 + 0.09 * std::cos(2 * kPi * m_time / 14);
    auto *eclipse = static_cast<EclipseMaterial *>(layers->eclipse->material());
    eclipse->uniforms.view[1] = float(m_time);
    eclipse->uniforms.view[2] = float(breath);
    eclipse->uniforms.view[3] = float(1 / scale);
    layers->eclipse->markDirty(QSGNode::DirtyMaterial);
    QMatrix4x4 turn;
    turn.translate(float(kCentre.x()), float(kCentre.y()));
    turn.rotate(float(std::fmod(m_dial, 360)), 0, 0, 1);
    turn.translate(float(-kCentre.x()), float(-kCentre.y()));
    layers->turn->setMatrix(turn);
    auto *waves = static_cast<WaveMaterial *>(layers->waves->material());
    waves->uniforms.view[1] = float(m_time);
    waves->uniforms.view[2] = 1;
    waves->uniforms.view[3] = float(1 / scale);
    layers->waves->markDirty(QSGNode::DirtyMaterial);
    return layers;
}
