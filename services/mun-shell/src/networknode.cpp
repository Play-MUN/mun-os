#include "networknode.h"

#include <QMatrix4x4>
#include <QSGMaterial>
#include <QSGMaterialShader>
#include <QTransform>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>

namespace {

constexpr qreal kTau = 6.283185307179586;
// The rings are polylines whose segments are at most this long at their
// widest (at the smallest ring, 230 px, they stray 0.08 px from the circle).
constexpr qreal kChord = 12;

// What the vertex shader is told each frame (shaders/network.vert, `buf`,
// std140): the matrix, then vec4s.
struct Uniforms {
    float view[4];      // orb x, orb y, one device pixel, opacity
    float motion[4];    // drift, turn, wobble, floor
    float extent[4];    // width, height, dash offsets
    float waves[NetworkNode::kWaves][4];
};
constexpr int kMatrixBytes = 64;

class NetworkMaterial : public QSGMaterial {
public:
    NetworkMaterial()
    {
        // Blended (premultiplied), and never merged with another node:
        // merging moves vertices on the CPU, and these are not positions.
        setFlag(Blending);
        setFlag(RequiresFullMatrix);
    }

    QSGMaterialType *type() const override
    {
        static QSGMaterialType type;
        return &type;
    }
    QSGMaterialShader *createShader(QSGRendererInterface::RenderMode) const override;

    Uniforms uniforms{};
};

class NetworkShader : public QSGMaterialShader {
public:
    NetworkShader()
    {
        setShaderFileName(VertexStage, QStringLiteral(":/shaders/network.vert.qsb"));
        setShaderFileName(FragmentStage, QStringLiteral(":/shaders/network.frag.qsb"));
    }

    bool updateUniformData(RenderState &state, QSGMaterial *newMaterial, QSGMaterial *) override
    {
        QByteArray *buffer = state.uniformData();
        Q_ASSERT(buffer->size() >= kMatrixBytes + int(sizeof(Uniforms)));
        char *data = buffer->data();
        if (state.isMatrixDirty()) {
            const QMatrix4x4 matrix = state.combinedMatrix();
            std::memcpy(data, matrix.constData(), kMatrixBytes);
        }
        Uniforms uniforms = static_cast<NetworkMaterial *>(newMaterial)->uniforms;
        uniforms.view[3] = state.opacity();
        std::memcpy(data + kMatrixBytes, &uniforms, sizeof(Uniforms));
        return true;
    }
};

QSGMaterialShader *NetworkMaterial::createShader(QSGRendererInterface::RenderMode) const
{
    return new NetworkShader;
}

// One vertex: its place at rest (the renderer's bounds; an orbit's actual
// place), then kind, index, side and the kind's value (network.vert).
struct Vertex {
    float x, y;
    float kind, index, side, value;
};

const QSGGeometry::AttributeSet &attributes()
{
    static const QSGGeometry::Attribute list[] = {
        QSGGeometry::Attribute::createWithAttributeType(0, 2, QSGGeometry::FloatType, QSGGeometry::PositionAttribute),
        QSGGeometry::Attribute::createWithAttributeType(1, 4, QSGGeometry::FloatType, QSGGeometry::UnknownAttribute),
    };
    static const QSGGeometry::AttributeSet set = {2, sizeof(Vertex), list};
    return set;
}

// Lines as the shader expects them: three vertices a point (side -1, 0, 1),
// two triangles each side between consecutive points.
class Builder {
public:
    // A line through `points` (kind, index and each point's value given).
    void line(const std::vector<QPointF> &rest, float kind, float index, const std::vector<float> &values)
    {
        const auto base = static_cast<quint32>(vertices.size());
        for (size_t i = 0; i < rest.size(); ++i)
            for (const float side : {-1.f, 0.f, 1.f})
                vertices.push_back({float(rest[i].x()), float(rest[i].y()), kind, index, side, values[i]});
        for (quint32 i = 1; i < rest.size(); ++i) {
            const quint32 a = base + 3 * (i - 1), b = base + 3 * i;
            for (const quint32 v : {a, a + 1, b, b, a + 1, b + 1, a + 1, a + 2, b + 1, b + 1, a + 2, b + 2})
                indices.push_back(v);
        }
    }

    std::vector<Vertex> vertices;
    std::vector<quint32> indices;
};

} // namespace

NetworkNode::NetworkNode()
{
    auto *geometry = new QSGGeometry(attributes(), 0, 0, QSGGeometry::UnsignedShortType);
    geometry->setDrawingMode(QSGGeometry::DrawTriangles);
    setGeometry(geometry);
    setMaterial(new NetworkMaterial);
    setFlags(OwnsGeometry | OwnsMaterial);
}

void NetworkNode::setShape(const QPointF &orb, const QSizeF &size, qreal pixel)
{
    if (orb == m_orb && size == m_size && qFuzzyCompare(pixel, m_pixel))
        return;
    m_orb = orb;
    m_size = size;
    m_pixel = pixel;
    build();
}

void NetworkNode::setFrame(const Frame &frame)
{
    Uniforms &u = static_cast<NetworkMaterial *>(material())->uniforms;
    u.view[0] = float(m_orb.x());
    u.view[1] = float(m_orb.y());
    u.view[2] = float(m_pixel);
    u.motion[0] = float(frame.drift);
    u.motion[1] = float(frame.turn);
    u.motion[2] = float(frame.wobble);
    u.motion[3] = float(frame.floor);
    u.extent[0] = float(m_size.width());
    u.extent[1] = float(m_size.height());
    u.extent[2] = float(frame.dash[0]);
    u.extent[3] = float(frame.dash[1]);
    for (int i = 0; i < kWaves; ++i) {
        u.waves[i][0] = float(frame.waves[i].x());
        u.waves[i][1] = float(frame.waves[i].y());
    }
    markDirty(DirtyMaterial);
}

void NetworkNode::build()
{
    Builder b;
    const QPointF o = m_orb;
    std::vector<QPointF> rest;
    std::vector<float> values;

    // Rings: angles around the orb, enough for the widest the ring drifts to.
    for (int k = 0; k < 10; ++k) {
        const int segments = static_cast<int>(std::ceil(kTau * (380 + 150 * k) / kChord));
        rest.clear();
        values.clear();
        for (int s = 0; s <= segments; ++s) {
            const qreal th = kTau * s / segments;
            rest.push_back(o + QPointF(std::cos(th), std::sin(th)) * (230 + 150 * k));
            values.push_back(float(th));
        }
        b.line(rest, 0, float(k), values);
    }

    // Spokes, and the eight stretches of each a wave can light; the value
    // is the end (0 near, 1 far), plus twice the wave's slot for a stretch.
    for (int k = 0; k < 36; ++k) {
        const qreal a = k / 36.0 * 6.283;
        const QPointF dir(std::cos(a), std::sin(a));
        b.line({o + dir * 240, o + dir * 1800}, 1, float(k), {0, 1});
        for (int slot = 0; slot < kWaves; ++slot)
            b.line({o + dir * 140, o + dir * 260}, 2, float(k), {float(2 * slot), float(2 * slot + 1)});
    }

    // Orbits: still, placed here with their sides; the value is the length
    // so far, counted along each segment's major axis (backdrop.cpp).
    const qreal half = std::max<qreal>(m_pixel, 1);
    for (int k = 0; k < 2; ++k) {
        const qreal rx = 330 + k * 70, ry = rx * 0.28;
        const QTransform turn = QTransform::fromTranslate(o.x(), o.y()).rotateRadians(-0.35 + k * 0.4);
        constexpr int kSegments = 160;
        std::vector<QPointF> points;
        for (int s = 0; s <= kSegments; ++s) {
            const qreal th = s * 6.29 / kSegments;
            points.push_back(turn.map(QPointF(std::cos(th) * rx, std::sin(th) * ry)));
        }
        const auto base = static_cast<quint32>(b.vertices.size());
        qreal walked = 0;
        for (int s = 0; s <= kSegments; ++s) {
            if (s > 0) {
                const QPointF step = points[s] - points[s - 1];
                walked += std::max(std::abs(step.x()), std::abs(step.y()));
            }
            const QPointF along = points[std::min(s + 1, kSegments)] - points[std::max(s - 1, 0)];
            const QPointF unit = along / std::hypot(along.x(), along.y());
            const QPointF across = QPointF(-unit.y(), unit.x()) * (half * std::max(std::abs(unit.x()), std::abs(unit.y())));
            for (const float side : {-1.f, 0.f, 1.f}) {
                const QPointF p = points[s] + across * side;
                b.vertices.push_back({float(p.x()), float(p.y()), 3, float(k), side, float(walked)});
            }
        }
        for (quint32 i = 1; i <= kSegments; ++i) {
            const quint32 a = base + 3 * (i - 1), c = base + 3 * i;
            for (const quint32 v : {a, a + 1, c, c, a + 1, c + 1, a + 1, a + 2, c + 1, c + 1, a + 2, c + 2})
                b.indices.push_back(v);
        }
    }

    // The floor: 14 lines across, the value their end (0 left, 1 right).
    const qreal w = m_size.width(), h = m_size.height();
    for (int k = 0; k < 14; ++k) {
        const qreal y = 880 + (h - 880) * (k / 14.0) * (k / 14.0);
        b.line({QPointF(0, y), QPointF(w, y)}, 4, float(k), {0, 1});
    }

    Q_ASSERT(b.vertices.size() < 65536);
    QSGGeometry *g = geometry();
    g->allocate(int(b.vertices.size()), int(b.indices.size()));
    std::memcpy(g->vertexData(), b.vertices.data(), b.vertices.size() * sizeof(Vertex));
    quint16 *index = g->indexDataAsUShort();
    for (size_t i = 0; i < b.indices.size(); ++i)
        index[i] = quint16(b.indices[i]);
    markDirty(DirtyGeometry);
}
