// Antialiased lines one device pixel wide, blended straight into an opaque
// image. Backdrop's network is dozens of long faint lines painted again on
// every frame of its drift; QPainter strokes each through its general span
// machinery (measured in the laboratory: 4 ms of a 6.7 ms frame went to the
// spokes alone), while these write each pixel they touch once.
//
// They draw what QPainter's antialiased one-pixel pen draws: at each step
// along the line, its coverage split between the two pixels nearest to it,
// the colour blended in by that coverage times the alpha.
//
// Coordinates are device pixels of the image, pixel (x, y) covering
// [x, x + 1) x [y, y + 1) as in QPainter; whatever falls outside the image
// is dropped. The image must be Format_RGB32 and not shared (writing to it
// must not detach it): the blend keeps the destination opaque.
#pragma once

#include <QImage>
#include <QPointF>
#include <QRgb>

namespace hairline {

// A straight line from `from` to `to`, its alpha (0 to 1) going linearly from
// `alphaFrom` to `alphaTo` along it. The pixels at `to` are left out, so a
// polyline drawn segment by segment does not blend its joints twice.
void line(QImage &image, const QPointF &from, const QPointF &to, QRgb colour, qreal alphaFrom, qreal alphaTo);

// A circle of `radius` around `centre`.
void circle(QImage &image, const QPointF &centre, qreal radius, QRgb colour, qreal alpha);

} // namespace hairline
