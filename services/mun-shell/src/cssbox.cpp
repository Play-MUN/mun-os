#include "cssbox.h"

#include "blur.h"

#include <QLinearGradient>
#include <QPainter>
#include <QPainterPath>
#include <QRadialGradient>
#include <QtMath>

#include <algorithm>
#include <cmath>

namespace css {

namespace {

QColor toColor(const QVariant &value)
{
    if (value.metaType() == QMetaType::fromType<QColor>())
        return value.value<QColor>();
    return QColor::fromString(value.toString());
}

qreal finite(const QVariant &value, qreal fallback = 0)
{
    bool ok = false;
    const qreal v = value.toReal(&ok);
    return ok && std::isfinite(v) ? v : fallback;
}

// CSS Backgrounds 3, 7.2: how a shadow's spread changes a corner's radius.
qreal spreadRadius(qreal radius, qreal spread)
{
    if (spread < 0)
        return std::max<qreal>(0, radius + spread);
    if (radius <= 0 || spread == 0)
        return radius;
    const qreal ratio = radius / spread;
    return ratio < 1 ? radius + spread * (1 + std::pow(ratio - 1, 3)) : radius + spread;
}

Radii spreadRadii(const Radii &r, qreal spread)
{
    return {spreadRadius(r.tl, spread), spreadRadius(r.tr, spread), spreadRadius(r.br, spread), spreadRadius(r.bl, spread)};
}

QGradientStops parseStops(const QVariantList &list)
{
    QGradientStops stops;
    for (const QVariant &entry : list) {
        const QVariantList pair = entry.toList();
        if (pair.size() != 2)
            continue;
        const QColor color = toColor(pair[1]);
        if (!color.isValid())
            continue;
        stops.append({std::clamp<qreal>(finite(pair[0]), 0, 1), color});
    }
    std::stable_sort(stops.begin(), stops.end(), [](const QGradientStop &a, const QGradientStop &b) { return a.first < b.first; });
    return stops;
}

// The brush of a CSS linear-gradient(angle, ...) or radial-gradient(circle at
// fx fy, ...) over `rect`, or none when the description is not usable. Qt
// interpolates gradient colours premultiplied, as CSS does.
QBrush gradientBrush(const QVariantMap &description, const QRectF &rect)
{
    const QGradientStops stops = parseStops(description.value(QStringLiteral("stops")).toList());
    if (stops.size() < 2 || rect.isEmpty())
        return {};
    const QString type = description.value(QStringLiteral("type"), QStringLiteral("linear")).toString();
    if (type == QLatin1String("radial")) {
        const QVariantList at = description.value(QStringLiteral("at")).toList();
        const qreal fx = at.size() == 2 ? finite(at[0], 0.5) : 0.5;
        const qreal fy = at.size() == 2 ? finite(at[1], 0.5) : 0.5;
        const QPointF centre(rect.x() + fx * rect.width(), rect.y() + fy * rect.height());
        // A circle's default size is farthest-corner.
        qreal radius = 0;
        for (const QPointF &corner : {rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()})
            radius = std::max(radius, std::hypot(corner.x() - centre.x(), corner.y() - centre.y()));
        QRadialGradient gradient(centre, radius);
        gradient.setStops(stops);
        return gradient;
    }
    // 0deg points up and angles turn clockwise; the gradient line crosses the
    // box's centre and is just long enough for its corners to meet the first
    // and last colours.
    const qreal angle = qDegreesToRadians(finite(description.value(QStringLiteral("angle")), 180));
    const QPointF direction(std::sin(angle), -std::cos(angle));
    const qreal length = std::abs(rect.width() * std::sin(angle)) + std::abs(rect.height() * std::cos(angle));
    const QPointF half = direction * (length / 2);
    QLinearGradient gradient(rect.center() - half, rect.center() + half);
    gradient.setStops(stops);
    return gradient;
}

} // namespace

Radii parseRadii(const QVariantList &list)
{
    auto value = [&](int i) { return std::max<qreal>(0, finite(list.value(i))); };
    if (list.size() == 4)
        return {value(0), value(1), value(2), value(3)};
    if (list.size() == 1)
        return {value(0), value(0), value(0), value(0)};
    return {};
}

QList<Shadow> parseShadows(const QVariantList &list)
{
    QList<Shadow> shadows;
    for (const QVariant &entry : list) {
        const QVariantMap map = entry.toMap();
        Shadow shadow;
        shadow.offset = {finite(map.value(QStringLiteral("x"))), finite(map.value(QStringLiteral("y")))};
        shadow.blur = std::max<qreal>(0, finite(map.value(QStringLiteral("blur"))));
        shadow.spread = finite(map.value(QStringLiteral("spread")));
        shadow.color = toColor(map.value(QStringLiteral("color")));
        if (shadow.color.isValid() && shadow.color.alpha() > 0)
            shadows << shadow;
    }
    return shadows;
}

Radii fitted(const Radii &r, const QSizeF &size)
{
    qreal f = 1;
    auto limit = [&f](qreal side, qreal a, qreal b) {
        if (a + b > side && a + b > 0)
            f = std::min(f, side / (a + b));
    };
    limit(size.width(), r.tl, r.tr);
    limit(size.width(), r.bl, r.br);
    limit(size.height(), r.tl, r.bl);
    limit(size.height(), r.tr, r.br);
    return {r.tl * f, r.tr * f, r.br * f, r.bl * f};
}

QPainterPath roundedRect(const QRectF &rect, const Radii &r)
{
    QPainterPath path;
    const qreal x = rect.x(), y = rect.y(), w = rect.width(), h = rect.height();
    path.moveTo(x + r.tl, y);
    path.lineTo(x + w - r.tr, y);
    if (r.tr > 0)
        path.arcTo(QRectF(x + w - 2 * r.tr, y, 2 * r.tr, 2 * r.tr), 90, -90);
    path.lineTo(x + w, y + h - r.br);
    if (r.br > 0)
        path.arcTo(QRectF(x + w - 2 * r.br, y + h - 2 * r.br, 2 * r.br, 2 * r.br), 0, -90);
    path.lineTo(x + r.bl, y + h);
    if (r.bl > 0)
        path.arcTo(QRectF(x, y + h - 2 * r.bl, 2 * r.bl, 2 * r.bl), 270, -90);
    path.lineTo(x, y + r.tl);
    if (r.tl > 0)
        path.arcTo(QRectF(x, y, 2 * r.tl, 2 * r.tl), 180, -90);
    path.closeSubpath();
    return path;
}

} // namespace css

// ---------------------------------------------------------------- Box

Box::Box(QQuickItem *parent) : QQuickPaintedItem(parent)
{
    setAntialiasing(true);
}

void Box::setColor(const QColor &color)
{
    if (color == m_color)
        return;
    m_color = color;
    update();
    emit changed();
}

void Box::setGradient(const QVariantMap &gradient)
{
    if (gradient == m_gradient)
        return;
    m_gradient = gradient;
    update();
    emit changed();
}

void Box::setRadii(const QVariantList &radii)
{
    if (radii == m_radiiList)
        return;
    m_radiiList = radii;
    m_radii = css::parseRadii(radii);
    update();
    emit changed();
}

void Box::setInsets(const QVariantList &insets)
{
    if (insets == m_insetList)
        return;
    m_insetList = insets;
    m_insets = css::parseShadows(insets);
    update();
    emit changed();
}

void Box::geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry)
{
    QQuickPaintedItem::geometryChange(newGeometry, oldGeometry);
    if (newGeometry.size() != oldGeometry.size())
        update();
}

void Box::paint(QPainter *painter)
{
    const QRectF rect = boundingRect();
    if (rect.isEmpty())
        return;
    const css::Radii radii = css::fitted(m_radii, rect.size());
    const QPainterPath shape = css::roundedRect(rect, radii);
    painter->setRenderHint(QPainter::Antialiasing);
    if (m_color.alpha() > 0)
        painter->fillPath(shape, m_color);
    if (!m_gradient.isEmpty()) {
        const QBrush brush = css::gradientBrush(m_gradient, rect);
        if (brush.style() != Qt::NoBrush)
            painter->fillPath(shape, brush);
    }
    if (m_insets.isEmpty())
        return;

    // An inset shadow is everything outside a hole (the box moved by the
    // offset and shrunk by the spread), blurred, seen only inside the box. The
    // first shadow of the list is on top, so they are painted in reverse.
    const qreal scale = blur::deviceScale(painter, rect.width());
    painter->save();
    painter->setClipPath(shape);
    for (auto it = m_insets.crbegin(); it != m_insets.crend(); ++it) {
        const css::Shadow &s = *it;
        const qreal reach = s.blur * 1.5 + std::abs(s.spread) + std::max(std::abs(s.offset.x()), std::abs(s.offset.y())) + 1;
        const QRectF area = rect.adjusted(-reach, -reach, reach, reach);
        const QRectF holeRect = rect.adjusted(s.spread, s.spread, -s.spread, -s.spread).translated(s.offset);
        const css::Radii holeRadii{std::max<qreal>(0, radii.tl - s.spread), std::max<qreal>(0, radii.tr - s.spread),
                                   std::max<qreal>(0, radii.br - s.spread), std::max<qreal>(0, radii.bl - s.spread)};
        QPainterPath band;
        band.setFillRule(Qt::OddEvenFill);
        band.addRect(area);
        if (holeRect.width() > 0 && holeRect.height() > 0)
            band.addPath(css::roundedRect(holeRect, css::fitted(holeRadii, holeRect.size())));
        if (s.blur <= 0)
            painter->fillPath(band, s.color);
        else
            blur::paintBlurredPath(painter, area, band, s.color, s.blur / 2, scale);
    }
    painter->restore();
}

// ---------------------------------------------------------------- BoxShadow

BoxShadow::BoxShadow(QQuickItem *parent) : QQuickPaintedItem(parent)
{
    setAntialiasing(true);
}

void BoxShadow::setBoxWidth(qreal width)
{
    if (qFuzzyCompare(width, m_boxWidth))
        return;
    m_boxWidth = width;
    update();
    emit changed();
}

void BoxShadow::setBoxHeight(qreal height)
{
    if (qFuzzyCompare(height, m_boxHeight))
        return;
    m_boxHeight = height;
    update();
    emit changed();
}

void BoxShadow::setRadii(const QVariantList &radii)
{
    if (radii == m_radiiList)
        return;
    m_radiiList = radii;
    m_radii = css::parseRadii(radii);
    update();
    emit changed();
}

void BoxShadow::setShadows(const QVariantList &shadows)
{
    if (shadows == m_shadowList)
        return;
    m_shadowList = shadows;
    m_shadows = css::parseShadows(shadows);
    recompute();
    update();
    emit changed();
}

void BoxShadow::recompute()
{
    qreal margin = 0;
    for (const css::Shadow &s : std::as_const(m_shadows))
        margin = std::max(margin, std::max(std::abs(s.offset.x()), std::abs(s.offset.y())) + std::max<qreal>(0, s.spread) + s.blur * 1.5);
    m_margin = std::ceil(margin) + (margin > 0 ? 1 : 0);
}

void BoxShadow::geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry)
{
    QQuickPaintedItem::geometryChange(newGeometry, oldGeometry);
    if (newGeometry.size() != oldGeometry.size())
        update();
}

void BoxShadow::paint(QPainter *painter)
{
    const QRectF box(m_margin, m_margin, m_boxWidth, m_boxHeight);
    if (box.isEmpty() || m_shadows.isEmpty())
        return;
    const css::Radii radii = css::fitted(m_radii, box.size());
    const QRectF area = boundingRect();
    const qreal scale = blur::deviceScale(painter, area.width());
    painter->setRenderHint(QPainter::Antialiasing);
    for (auto it = m_shadows.crbegin(); it != m_shadows.crend(); ++it) {
        const css::Shadow &s = *it;
        const QRectF shapeRect = box.adjusted(-s.spread, -s.spread, s.spread, s.spread).translated(s.offset);
        if (shapeRect.width() <= 0 || shapeRect.height() <= 0)
            continue;
        const QPainterPath shape = css::roundedRect(shapeRect, css::fitted(css::spreadRadii(radii, s.spread), shapeRect.size()));
        if (s.blur <= 0)
            painter->fillPath(shape, s.color);
        else
            blur::paintBlurredPath(painter, area, shape, s.color, s.blur / 2, scale);
    }
    // An outer shadow is never seen through the box that casts it.
    painter->setCompositionMode(QPainter::CompositionMode_Clear);
    painter->fillPath(css::roundedRect(box, radii), Qt::black);
}
