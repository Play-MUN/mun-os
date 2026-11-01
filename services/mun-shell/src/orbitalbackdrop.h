// OrbitalBackdrop paints Home's background in the orbital design: the night,
// darker downwards, with the eclipse's warm haze in its upper left; the
// eclipse itself, the lit edge of the outer ring and its halo; the orbit,
// rings whose gaps widen outwards (18, 26, 34, 42, 50 and 58 px), a faint
// polar grid and axes and the nodes, its outer part fading before the menu,
// its centre left clear; the ring of dots; and two waves along the bottom,
// the front one's edge lit by the eclipse on the left and again on the
// right, where it passes behind the panel.
//
// Two drawing paths (src/renderinfo.h):
// - A graphics API: the night and its haze are a mesh with a colour at each
//   vertex; the eclipse is a quad over its corner whose shader computes the
//   edge and halo (shaders/eclipse.*); the orbit and the ring of dots are
//   painted once into textures, the images let go once uploaded, the dots
//   turned by a transform; the waves are a mesh built once whose vertex
//   shader places the curves from the time, their edges' light computed only
//   on a narrow strip around them (shaders/wave.*). A frame of motion costs
//   the CPU a few numbers and the GPU little blending.
// - Qt Quick's software renderer: everything is painted once into one image,
//   the waves at rest, and shown still.
// The motion is slow (the waves sway a few pixels over tens of seconds, the
// eclipse breathes over 14 s, the dots turn once in four minutes), so it
// runs at SceneClock's 10 frames per second, and with `running` false it
// stops where it is. The light adds to what is beneath it, as light does.
//
// Geometry is in logical pixels of the 1920x1080 design; the item is opaque
// and meant to fill the window. Properties and motion on the GUI thread;
// updatePaintNode() on the render thread with the GUI thread blocked.
#pragma once

#include <QImage>
#include <QPointF>
#include <QQuickItem>
#include <QtQml/qqmlregistration.h>

class OrbitalBackdrop : public QQuickItem {
    Q_OBJECT
    QML_ELEMENT
    // Whether the waves, the eclipse and the dots move (Reduce motion: no).
    Q_PROPERTY(bool running READ running WRITE setRunning NOTIFY runningChanged)
    // Seconds of motion so far, on the layer's ticks (10 frames a second):
    // what else moves with the background (the menu's icons) moves by it, so
    // the window still draws only those frames. Still while not running.
    Q_PROPERTY(qreal time READ time NOTIFY timeChanged)

public:
    // The orbit's centre, the rings' radii and where the menu begins: the
    // menu places its nodes on nodeRing and its entries from menuX.
    static constexpr qreal kCentreX = 470;
    static constexpr qreal kCentreY = 540;   // the canvas's middle: the menu and the panel share it
    static constexpr qreal kRadii[] = {148, 166, 192, 226, 268, 318, 376};
    static constexpr qreal kNodeRing = 268;
    static constexpr qreal kOuterRing = 376;
    static constexpr qreal kMenuX = 772;

    explicit OrbitalBackdrop(QQuickItem *parent = nullptr);
    ~OrbitalBackdrop() override;

    bool running() const { return m_running; }
    void setRunning(bool running);
    qreal time() const { return m_time; }

signals:
    void runningChanged();
    void timeChanged();

protected:
    QSGNode *updatePaintNode(QSGNode *old, UpdatePaintNodeData *) override;
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;

private:
    void onTick(quint64 frame);
    void updateDemand();

    bool m_running = true;
    qreal m_time = 0;      // seconds of motion so far
    qreal m_last = -1;     // the clock's time at the last tick, while moving
    bool m_reset = true;   // the nodes are made again (size, scale or path changed)
};
