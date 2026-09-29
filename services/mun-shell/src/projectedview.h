// ProjectedView shows another item, `source`, through a perspective
// transform (the design's panel leans back 16 degrees). Qt Quick's software
// renderer draws projective transforms slowly: every glyph and image of the
// item goes through a per-pixel perspective path, again on every frame that
// repaints the region, and Home's background repaints the whole screen
// several times a second. Here the source is rendered flat once per change,
// projected once into an image cropped to what it shows, and from then on
// each frame costs a plain copy of that image (a full-size image would blend
// the empty margins around the projection on every frame too).
//
// The source stays in the scene with opacity 0, so the window never draws
// it; QML calls refresh() whenever something it shows changes (a grab
// renders the item's content regardless of its own opacity and position).
// `projection` maps the source's coordinates to this item's; the source must
// fit inside this item once projected. A refresh while a grab is in flight
// takes one more grab when it lands. GUI thread only.
//
// Measured with Qt 6.8.2's software renderer: at a device pixel ratio under 1
// (the 1920x1080 canvas on a 1280x720 display) a grab renders the item at the
// right scale but only its first `ratio` of width and height, whatever size
// is asked for; the source must then be larger by 1 / ratio, the rest empty
// (see DetailPanel.qml).
#pragma once

#include <QImage>
#include <QMatrix4x4>
#include <QPointer>
#include <QQuickItem>
#include <QSharedPointer>
#include <QtQml/qqmlregistration.h>

class QQuickItemGrabResult;

class ProjectedView : public QQuickItem {
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(QQuickItem *source READ source WRITE setSource NOTIFY sourceChanged)
    Q_PROPERTY(QMatrix4x4 projection READ projection WRITE setProjection NOTIFY projectionChanged)

public:
    explicit ProjectedView(QQuickItem *parent = nullptr);

    Q_INVOKABLE void refresh();
    // The point of the source under `point` of this item (for pointer input
    // on what the view shows).
    Q_INVOKABLE QPointF toSource(const QPointF &point) const;

    QQuickItem *source() const { return m_source; }
    void setSource(QQuickItem *source);
    QMatrix4x4 projection() const { return m_projection; }
    void setProjection(const QMatrix4x4 &projection);

signals:
    void sourceChanged();
    void projectionChanged();

protected:
    QSGNode *updatePaintNode(QSGNode *old, UpdatePaintNodeData *data) override;
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;

private:
    void grab();
    void repaint();

    QPointer<QQuickItem> m_source;
    QMatrix4x4 m_projection;
    QImage m_image;                              // the source, flat, as grabbed
    QSharedPointer<QQuickItemGrabResult> m_pending;
    bool m_again = false;
    bool m_stale = true;                         // the image shown is out of date
};
