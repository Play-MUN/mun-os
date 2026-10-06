// The front of a MUN Shape transition (docs/shape.md, "transition"): where
// the game's world has arrived at a moment of it, and how far each surface
// has been reached. One definition for the world that draws the front
// (ShapeWorld), the surfaces that change as it passes (Shape::reach, from
// QML) and the card object that changes first.
//
// `progress` runs from 0 (MUN) to 1 (the world whole) in time, linearly; the
// front's own easing is here. Coordinates are the 1920x1080 design canvas's.
//
// - tide: a circle from the card object (the orb) that grows to the canvas's
//   farthest corner, slow to leave, fast across, slow to arrive; a surface
//   is reached as the front crosses it, from its nearest point to its
//   farthest.
// - sweep: a vertical front from the left edge to the right one.
// - fade: everything at once.
//
// Pure functions; any thread.
#pragma once

#include <QLineF>
#include <QPointF>
#include <QRectF>
#include <QString>

#include <algorithm>
#include <cmath>

namespace shapefront {

constexpr qreal kCanvasWidth = 1920, kCanvasHeight = 1080;
// How wide the front is where it changes a surface: a surface narrower than
// this still changes over this much of the front's travel.
constexpr qreal kSpan = 180;
// The card object changes within the first stretch of a tide.
constexpr qreal kObjectReach = 260;

inline qreal ease(qreal p)
{
    p = std::clamp<qreal>(p, 0, 1);
    return p < 0.5 ? 2 * p * p : 1 - std::pow(-2 * p + 2, 2) / 2;
}

inline qreal smooth(qreal u)
{
    u = std::clamp<qreal>(u, 0, 1);
    return u * u * (3 - 2 * u);
}

// The tide's largest radius: past the farthest corner, with its glow.
inline qreal tideReach(const QPointF &orb)
{
    qreal far = 0;
    for (const QPointF corner : {QPointF(0, 0), QPointF(kCanvasWidth, 0), QPointF(0, kCanvasHeight),
                                 QPointF(kCanvasWidth, kCanvasHeight)})
        far = std::max(far, QLineF(orb, corner).length());
    return far + kSpan;
}

inline qreal tideRadius(const QPointF &orb, qreal progress)
{
    return ease(progress) * tideReach(orb);
}

// The sweep's x: from a span before the left edge to a span past the right.
inline qreal sweepX(qreal progress)
{
    return -kSpan + ease(progress) * (kCanvasWidth + 2 * kSpan);
}

// How far the transition has reached `box` (canvas coordinates), 0 to 1.
inline qreal reach(const QString &kind, const QPointF &orb, qreal progress, const QRectF &box)
{
    if (kind == QLatin1String("tide")) {
        const qreal nearX = std::clamp(orb.x(), box.left(), box.right());
        const qreal nearY = std::clamp(orb.y(), box.top(), box.bottom());
        const qreal nearest = QLineF(orb, QPointF(nearX, nearY)).length();
        qreal farthest = 0;
        for (const QPointF corner : {box.topLeft(), box.topRight(), box.bottomLeft(), box.bottomRight()})
            farthest = std::max(farthest, QLineF(orb, corner).length());
        const qreal span = std::max(kSpan, farthest - nearest);
        return std::clamp<qreal>((tideRadius(orb, progress) - nearest) / span, 0, 1);
    }
    if (kind == QLatin1String("sweep")) {
        const qreal span = std::max(kSpan, box.width());
        return std::clamp<qreal>((sweepX(progress) - box.left()) / span, 0, 1);
    }
    return std::clamp<qreal>(progress, 0, 1);
}

// The card object's own progress: it changes first.
inline qreal objectReach(const QString &kind, const QPointF &orb, qreal progress)
{
    if (kind == QLatin1String("tide"))
        return smooth(tideRadius(orb, progress) / kObjectReach);
    if (kind == QLatin1String("sweep"))
        return reach(kind, orb, progress, QRectF(orb.x() - 320, orb.y() - 320, 640, 640));
    return smooth(progress);
}

} // namespace shapefront
