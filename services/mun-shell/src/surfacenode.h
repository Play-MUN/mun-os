// Box and BoxShadow (cssbox.h) on the graphics path (src/renderinfo.h): the
// GPU draws them from a few numbers on every frame, instead of the CPU
// painting an image of them on every change of size or style. A panel that
// grows, a bar whose glow changes colour, cost no painting and keep no
// image.
//
// - BoxNode: the rounded box (each corner's radius, fitted as CSS fits
//   them), its colour, its gradient (linear at any angle or circular radial,
//   up to four stops, interpolated premultiplied as Qt does) and up to four
//   inset shadows without blur, in the order Box::paint() paints them
//   (shaders/box.frag).
// - ShadowNode: up to four outer shadows, each its shape (the box moved and
//   spread, its radii spread as CSS spreads them) blurred by a Gaussian of
//   standard deviation blur / 2, and nothing under the box
//   (shaders/shadow.frag). The blur is computed: exactly across, by eight
//   samples along, the corners following each row.
// Edges are antialiased over one device pixel (`pixel`, in item units) from
// the shape's distance. What they cannot draw (an inset with blur, more
// stops or shadows) Box and BoxShadow paint as before: supports() says.
//
// Render thread only, as any scene-graph node.
#pragma once

#include "cssbox.h"

#include <QSGGeometryNode>

class BoxNode : public QSGGeometryNode {
public:
    BoxNode();

    static bool supports(const css::Gradient &gradient, const QList<css::Shadow> &insets);
    // `rect` is the item's; `radii` already fitted to it.
    void set(const QRectF &rect, const css::Radii &radii, const QColor &color, const css::Gradient &gradient,
             const QList<css::Shadow> &insets, qreal pixel);
};

class ShadowNode : public QSGGeometryNode {
public:
    ShadowNode();

    static bool supports(const QList<css::Shadow> &shadows);
    // `area` is the item's; `box` the box casting the shadows within it,
    // `radii` already fitted to it.
    void set(const QRectF &area, const QRectF &box, const css::Radii &radii, const QList<css::Shadow> &shadows,
             qreal pixel);
};
