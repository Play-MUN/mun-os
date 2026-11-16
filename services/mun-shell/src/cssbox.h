// Box and BoxShadow paint the shell's surfaces with the semantics of the CSS
// the design is written in, which Qt Quick's Rectangle does not have: corner
// radii scaled down together when they do not fit, linear gradients at any
// angle, circular radial gradients, and box shadows (outer and inset, with
// blur and spread). Each item paints once per change of geometry or
// parameters into its own cached image; drawing it again costs a copy, so a
// still screen costs nothing.
//
// Lengths are logical pixels, angles degrees. GUI thread only, like any
// Qt Quick item; painting happens where Qt Quick's software renderer calls it.
// On a graphics API the GPU draws them instead (src/surfacenode.h): no image,
// and nothing to paint when they change.
//
// Values arrive from QML as plain lists and maps, and a malformed entry is
// skipped rather than trusted: a shadow is {x, y, blur, spread, color}, a
// gradient {type: "linear", angle, stops} or {type: "radial", at: [fx, fy],
// stops}, with stops as [[position, color], ...]; radii are [all] or
// [top-left, top-right, bottom-right, bottom-left], as in CSS.
#pragma once

#include <QBrush>
#include <QColor>
#include <QGradient>
#include <QQuickPaintedItem>
#include <QVariantList>
#include <QVariantMap>
#include <QtQml/qqmlregistration.h>

namespace css {

struct Shadow {
    QPointF offset;
    qreal blur = 0;    // CSS blur radius: a Gaussian of standard deviation blur / 2
    qreal spread = 0;
    QColor color;
};

// Corner radii, clockwise from the top left.
struct Radii {
    qreal tl = 0, tr = 0, br = 0, bl = 0;
};

// A CSS linear-gradient(angle, ...) from `start` to `end`, or a
// radial-gradient(circle at ..., ...) around `start` of `radius`, over a box.
struct Gradient {
    enum Kind { None, Linear, Radial };
    Kind kind = None;
    QPointF start, end;
    qreal radius = 0;
    QGradientStops stops;
};

Radii parseRadii(const QVariantList &list);
QList<Shadow> parseShadows(const QVariantList &list);
// A gradient description from QML over `rect`; Gradient::None when it is
// not usable.
Gradient gradientFor(const QVariantMap &description, const QRectF &rect);
QBrush gradientBrush(const Gradient &gradient);
// The radii a box of `size` really draws with (CSS Backgrounds 3, 5.5).
Radii fitted(const Radii &radii, const QSizeF &size);
// How a shadow's spread changes the corners' radii (CSS Backgrounds 3, 7.2).
Radii spreadRadii(const Radii &radii, qreal spread);
QPainterPath roundedRect(const QRectF &rect, const Radii &radii);

} // namespace css

// A surface: background (colour, then gradient over it) and inset shadows,
// inside rounded corners. Children draw on top.
class Box : public QQuickPaintedItem {
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(QColor color READ color WRITE setColor NOTIFY changed)
    Q_PROPERTY(QVariantMap gradient READ gradient WRITE setGradient NOTIFY changed)
    Q_PROPERTY(QVariantList radii READ radii WRITE setRadii NOTIFY changed)
    Q_PROPERTY(QVariantList insets READ insets WRITE setInsets NOTIFY changed)

public:
    explicit Box(QQuickItem *parent = nullptr);
    void paint(QPainter *painter) override;

    QColor color() const { return m_color; }
    void setColor(const QColor &color);
    QVariantMap gradient() const { return m_gradient; }
    void setGradient(const QVariantMap &gradient);
    QVariantList radii() const { return m_radiiList; }
    void setRadii(const QVariantList &radii);
    QVariantList insets() const { return m_insetList; }
    void setInsets(const QVariantList &insets);

signals:
    void changed();

protected:
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;
    // On a graphics API, a BoxNode (src/surfacenode.h) when it can draw the
    // box; otherwise the painted image.
    QSGNode *updatePaintNode(QSGNode *old, UpdatePaintNodeData *data) override;

private:
    QColor m_color = Qt::transparent;
    QVariantMap m_gradient;
    QVariantList m_radiiList, m_insetList;
    css::Radii m_radii;
    QList<css::Shadow> m_insets;
};

// Outer shadows of a box of boxWidth x boxHeight. The item is larger than the
// box by `margin` on every side (QML places it at -margin), because a blurred
// shadow reaches beyond the box; like CSS, nothing is drawn under the box
// itself, so a translucent surface over it stays clean.
class BoxShadow : public QQuickPaintedItem {
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(qreal boxWidth READ boxWidth WRITE setBoxWidth NOTIFY changed)
    Q_PROPERTY(qreal boxHeight READ boxHeight WRITE setBoxHeight NOTIFY changed)
    Q_PROPERTY(QVariantList radii READ radii WRITE setRadii NOTIFY changed)
    Q_PROPERTY(QVariantList shadows READ shadows WRITE setShadows NOTIFY changed)
    Q_PROPERTY(qreal margin READ margin NOTIFY changed)

public:
    explicit BoxShadow(QQuickItem *parent = nullptr);
    void paint(QPainter *painter) override;

    qreal boxWidth() const { return m_boxWidth; }
    void setBoxWidth(qreal width);
    qreal boxHeight() const { return m_boxHeight; }
    void setBoxHeight(qreal height);
    QVariantList radii() const { return m_radiiList; }
    void setRadii(const QVariantList &radii);
    QVariantList shadows() const { return m_shadowList; }
    void setShadows(const QVariantList &shadows);
    qreal margin() const { return m_margin; }

signals:
    void changed();

protected:
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;
    // On a graphics API, a ShadowNode (src/surfacenode.h) when it can draw
    // the shadows; otherwise the painted image.
    QSGNode *updatePaintNode(QSGNode *old, UpdatePaintNodeData *data) override;

private:
    void recompute();

    qreal m_boxWidth = 0, m_boxHeight = 0, m_margin = 0;
    QVariantList m_radiiList, m_shadowList;
    css::Radii m_radii;
    QList<css::Shadow> m_shadows;
};
