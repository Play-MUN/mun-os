#include "orbitalcard.h"

#include "blur.h"

#include <QPainter>
#include <QPainterPath>

#include <algorithm>
#include <cmath>

namespace {

constexpr qreal kPi = 3.14159265358979323846;
// The disc's hole, as a share of its radius (OrbitalIcon's "disk").
constexpr qreal kHole = 2.6 / 9.5;
// The rings: the orbital design's cream (qml/Theme.qml, palette.moonlight 100).
const QColor kCream(0xFB, 0xED, 0xD4);

// The disc's outline about `centre`: a circle, its edge swelling and
// receding by up to about a tenth of the radius as `organic` grows (0 to 1).
QPainterPath outlineOf(QPointF centre, qreal radius, qreal organic)
{
    constexpr int kSteps = 120;
    QPainterPath path;
    for (int i = 0; i <= kSteps; ++i) {
        const qreal th = 2 * kPi * i / kSteps;
        const qreal k = 1 + organic * (0.06 * std::sin(3 * th + 0.6) + 0.03 * std::sin(5 * th + 1.7));
        const QPointF point = centre + QPointF(radius * k * std::cos(th), radius * k * std::sin(th));
        i ? path.lineTo(point) : path.moveTo(point);
    }
    path.closeSubpath();
    return path;
}

} // namespace

OrbitalCard::OrbitalCard(QQuickItem *parent) : QQuickPaintedItem(parent)
{
    setAntialiasing(true);
}

void OrbitalCard::setCardWindow(const QVariant &window)
{
    const QImage image = window.metaType().id() == QMetaType::QImage ? window.value<QImage>() : QImage();
    if (image.cacheKey() == m_window.cacheKey() && image.isNull() == m_window.isNull())
        return;
    m_window = image;
    emit cardChanged();
    update();
}

void OrbitalCard::setCardShape(const QString &shape)
{
    const QString valid = shape == QLatin1String("organic") ? shape : QStringLiteral("card");
    if (valid == m_cardShape)
        return;
    m_cardShape = valid;
    emit cardChanged();
    update();
}

void OrbitalCard::setMorph(qreal morph)
{
    morph = std::isfinite(morph) ? std::clamp<qreal>(morph, 0, 1) : 0;
    if (qFuzzyCompare(morph + 1, m_morph + 1))
        return;
    m_morph = morph;
    emit cardChanged();
    update();
}

void OrbitalCard::setCardGlow(const QColor &glow)
{
    if (glow == m_glow)
        return;
    m_glow = glow;
    emit cardChanged();
    update();
}

void OrbitalCard::setCardReveal(qreal reveal)
{
    reveal = std::isfinite(reveal) ? std::clamp<qreal>(reveal, 0, 1) : 0;
    if (qFuzzyCompare(reveal + 1, m_reveal + 1))
        return;
    const int before = revealStep();
    m_reveal = reveal;
    emit cardChanged();
    if (revealStep() != before)
        update();
}

void OrbitalCard::setRadius(qreal radius)
{
    if (!(radius > 0) || qFuzzyCompare(radius, m_radius))
        return;
    m_radius = radius;
    emit cardChanged();
    update();
}

void OrbitalCard::paint(QPainter *painter)
{
    const int step = revealStep();
    if (m_window.isNull() || step <= 0)
        return;
    const qreal reveal = step / 10.0;
    const qreal scale = blur::deviceScale(painter, width());
    const QPointF centre(width() / 2, height() / 2);
    const qreal organic = m_cardShape == QLatin1String("organic") ? m_morph * reveal : 0;
    const QPainterPath outline = outlineOf(centre, m_radius, organic);
    const qreal hole = m_radius * kHole;
    painter->setRenderHint(QPainter::Antialiasing);
    painter->setRenderHint(QPainter::SmoothPixmapTransform);

    // Its light, under it.
    if (m_glow.isValid() && m_glow.alpha() > 0) {
        QColor light = m_glow;
        light.setAlphaF(float(0.55 * reveal));
        blur::paintBlurredPath(painter, QRectF(0, 0, width(), height()), outline, light, 18, scale);
    }
    // The window covering the disc, its hole cut out.
    QPainterPath face = outline;
    QPainterPath middle;
    middle.addEllipse(centre, hole, hole);
    face = face.subtracted(middle);
    painter->save();
    painter->setClipPath(face);
    painter->setOpacity(reveal);
    const QRectF box = outline.boundingRect();
    const QSizeF size = QSizeF(m_window.size()).scaled(box.size(), Qt::KeepAspectRatioByExpanding);
    painter->drawImage(QRectF(box.center() - QPointF(size.width() / 2, size.height() / 2), size), m_window);
    painter->restore();
    // The hole shows what is behind, as MUN's disk does, not the light.
    painter->save();
    painter->setOpacity(1);
    painter->setCompositionMode(QPainter::CompositionMode_Clear);
    painter->fillPath(middle, Qt::transparent);
    painter->restore();
    // Fine rings at its edge and around its hole.
    painter->setOpacity(reveal);
    painter->setBrush(Qt::NoBrush);
    QColor ring = kCream;
    ring.setAlphaF(0.9f);
    painter->setPen(QPen(ring, 2));
    painter->drawPath(outline);
    ring.setAlphaF(0.7f);
    painter->setPen(QPen(ring, 1.5));
    painter->drawEllipse(centre, hole, hole);
}
