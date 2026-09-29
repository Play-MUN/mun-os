// Gaussian-like blur for the shell's painted layers (shadows, glows). The
// software scene graph has no effects, so the soft light of the design is
// computed here on small images, once per change of what casts it.
#pragma once

#include <QColor>
#include <QImage>
#include <QRectF>

class QPainter;
class QPainterPath;

namespace blur {

// Approximates a Gaussian of standard deviation `sigma` (in pixels of `image`)
// with three box blurs in each direction. `image` must be
// Format_ARGB32_Premultiplied; it is blurred in place, and outside it counts
// as transparent. A sigma under 0.5 leaves it untouched.
void gaussian(QImage &image, double sigma);

// Device pixels per logical pixel of the painter's target, for an item
// `logicalWidth` wide painting into all of it.
qreal deviceScale(const QPainter *painter, qreal logicalWidth);

// Fills `path` with `color`, blurred by a Gaussian of `sigma` logical pixels,
// and draws the result over `area`; both are in the painter's current
// coordinates, and `scale` is device pixels per logical pixel there. Wide
// blurs are computed at a lower resolution, `detail` pixels per sigma: they
// have no detail to lose, and a glow repainted every frame can take fewer.
void paintBlurredPath(QPainter *painter, const QRectF &area, const QPainterPath &path, const QColor &color,
                      qreal sigma, qreal scale, qreal detail = 4);

} // namespace blur
