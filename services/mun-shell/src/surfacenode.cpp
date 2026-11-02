#include "surfacenode.h"

#include <QMatrix4x4>
#include <QSGMaterial>
#include <QSGMaterialShader>

#include <algorithm>
#include <cstring>

namespace {

constexpr int kLimit = 4;          // stops, insets, shadows
constexpr int kMatrixBytes = 64;

using Vec4 = std::array<float, 4>;

Vec4 premultiplied(const QColor &color)
{
    const float a = float(color.alphaF());
    return {float(color.redF()) * a, float(color.greenF()) * a, float(color.blueF()) * a, a};
}

Vec4 rectOf(const QRectF &rect)
{
    return {float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height())};
}

Vec4 radiiOf(const css::Radii &r)
{
    return {float(r.tl), float(r.tr), float(r.br), float(r.bl)};
}

// shaders/box.frag's uniform block after the matrix (std140: vec4s).
struct BoxUniforms {
    Vec4 view{};    // opacity, pixel, gradient kind, insets
    Vec4 rect{};
    Vec4 radii{};
    Vec4 color{};
    Vec4 line{};
    Vec4 stopAt{};
    Vec4 stops[kLimit]{};
    Vec4 holes[kLimit]{};
    Vec4 holeRadii[kLimit]{};
    Vec4 insetColors[kLimit]{};
};

// shaders/shadow.frag's.
struct ShadowUniforms {
    Vec4 view{};    // opacity, pixel, shadows
    Vec4 box{};
    Vec4 boxRadii{};
    Vec4 shapes[kLimit]{};
    Vec4 shapeRadii[kLimit]{};
    Vec4 sigmas{};
    Vec4 colors[kLimit]{};
};

// A material holding its shader's uniforms, written whole on every draw
// (they are a few hundred bytes); the opacity is the scene graph's.
template<typename Uniforms>
class SurfaceMaterial : public QSGMaterial {
public:
    SurfaceMaterial(const char *vertex, const char *fragment) : m_vertex(vertex), m_fragment(fragment)
    {
        // Blended (premultiplied), and never merged with another node:
        // merging moves vertices out of the item's coordinates, which the
        // shader draws in.
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
            // The buffer is this node's own and holds what it was given
            // last: it is written, and uploaded, only when that changed. A
            // surface that does not change costs nothing more each frame.
            QByteArray *buffer = state.uniformData();
            Q_ASSERT(buffer->size() >= kMatrixBytes + int(sizeof(Uniforms)));
            char *data = buffer->data();
            bool changed = false;
            if (state.isMatrixDirty()) {
                const QMatrix4x4 matrix = state.combinedMatrix();
                std::memcpy(data, matrix.constData(), kMatrixBytes);
                changed = true;
            }
            Uniforms uniforms = static_cast<SurfaceMaterial *>(newMaterial)->uniforms;
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

using BoxMaterial = SurfaceMaterial<BoxUniforms>;
using ShadowMaterial = SurfaceMaterial<ShadowUniforms>;

// The node's quad over `rect`, in the item's coordinates.
void setQuad(QSGGeometryNode *node, const QRectF &rect)
{
    QSGGeometry *geometry = node->geometry();
    QSGGeometry::updateRectGeometry(geometry, rect);
    node->markDirty(QSGNode::DirtyGeometry);
}

QSGGeometry *quad()
{
    auto *geometry = new QSGGeometry(QSGGeometry::defaultAttributes_Point2D(), 4);
    geometry->setDrawingMode(QSGGeometry::DrawTriangleStrip);
    return geometry;
}

} // namespace

BoxNode::BoxNode()
{
    setGeometry(quad());
    setMaterial(new BoxMaterial(":/shaders/box.vert.qsb", ":/shaders/box.frag.qsb"));
    setFlags(OwnsGeometry | OwnsMaterial);
}

bool BoxNode::supports(const css::Gradient &gradient, const QList<css::Shadow> &insets)
{
    if (gradient.kind != css::Gradient::None && gradient.stops.size() > kLimit)
        return false;
    if (insets.size() > kLimit)
        return false;
    return std::all_of(insets.cbegin(), insets.cend(), [](const css::Shadow &s) { return s.blur <= 0; });
}

void BoxNode::set(const QRectF &rect, const css::Radii &radii, const QColor &color, const css::Gradient &gradient,
                  const QList<css::Shadow> &insets, qreal pixel)
{
    BoxUniforms &u = static_cast<BoxMaterial *>(material())->uniforms;
    u = {};
    u.view = {1, float(pixel), float(gradient.kind), float(insets.size())};
    u.rect = rectOf(rect);
    u.radii = radiiOf(radii);
    u.color = premultiplied(color);
    if (gradient.kind == css::Gradient::Linear)
        u.line = {float(gradient.start.x()), float(gradient.start.y()), float(gradient.end.x()), float(gradient.end.y())};
    else if (gradient.kind == css::Gradient::Radial)
        u.line = {float(gradient.start.x()), float(gradient.start.y()), float(gradient.radius), 0};
    u.stopAt = {-1, -1, -1, -1};
    for (int i = 0; i < gradient.stops.size() && i < kLimit; ++i) {
        u.stopAt[i] = float(gradient.stops[i].first);
        u.stops[i] = premultiplied(gradient.stops[i].second);
    }
    // Each inset's hole, as Box::paint() makes it.
    for (int i = 0; i < insets.size() && i < kLimit; ++i) {
        const css::Shadow &s = insets[i];
        const QRectF hole = rect.adjusted(s.spread, s.spread, -s.spread, -s.spread).translated(s.offset);
        const css::Radii holeRadii{std::max<qreal>(0, radii.tl - s.spread), std::max<qreal>(0, radii.tr - s.spread),
                                   std::max<qreal>(0, radii.br - s.spread), std::max<qreal>(0, radii.bl - s.spread)};
        u.holes[i] = hole.width() > 0 && hole.height() > 0 ? rectOf(hole) : Vec4{};
        u.holeRadii[i] = radiiOf(css::fitted(holeRadii, hole.size()));
        u.insetColors[i] = premultiplied(s.color);
    }
    setQuad(this, rect);
    markDirty(DirtyMaterial);
}

ShadowNode::ShadowNode()
{
    setGeometry(quad());
    setMaterial(new ShadowMaterial(":/shaders/shadow.vert.qsb", ":/shaders/shadow.frag.qsb"));
    setFlags(OwnsGeometry | OwnsMaterial);
}

bool ShadowNode::supports(const QList<css::Shadow> &shadows)
{
    return shadows.size() <= kLimit;
}

void ShadowNode::set(const QRectF &area, const QRectF &box, const css::Radii &radii, const QList<css::Shadow> &shadows,
                     qreal pixel)
{
    ShadowUniforms &u = static_cast<ShadowMaterial *>(material())->uniforms;
    u = {};
    u.box = rectOf(box);
    u.boxRadii = radiiOf(radii);
    // Each shadow's shape, as BoxShadow::paint() makes it; one that is
    // empty draws nothing.
    int count = 0;
    for (const css::Shadow &s : shadows) {
        const QRectF shape = box.adjusted(-s.spread, -s.spread, s.spread, s.spread).translated(s.offset);
        if (shape.width() <= 0 || shape.height() <= 0 || count == kLimit)
            continue;
        u.shapes[count] = rectOf(shape);
        u.shapeRadii[count] = radiiOf(css::fitted(css::spreadRadii(radii, s.spread), shape.size()));
        u.sigmas[count] = float(s.blur / 2);
        u.colors[count] = premultiplied(s.color);
        ++count;
    }
    u.view = {1, float(pixel), float(count), 0};
    setQuad(this, area);
    markDirty(DirtyMaterial);
}
