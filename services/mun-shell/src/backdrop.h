// Backdrop paints Home's background, the design's network of light: the
// night gradient with a blue and a patina glow, an ambient light whose colour
// follows the local hour, and, around the orb (the centre of the large icon),
// ten rings drifting outwards, thirty-six slowly turning spokes, two dashed
// orbits and a floor of lines. ripple() sends a wave out of the orb through
// the rings and spokes; Home sends one whenever the chosen entry changes.
//
// Cost: every frame of this layer repaints the whole screen, so it is built
// to be cheap. The gradients are painted once into a base image (again only
// when the ambient light has visibly changed); a frame copies the base into
// one of two images of its own, draws the rings and spokes with `hairline`
// (a direct antialiased rasterizer; QPainter took most of the frame on
// them) and the orbits and floor with QPainter, and hands the image to the
// scene graph, which shows it without copying it (the two images take
// turns, so the one shown is never written). The drift runs at driftRate
// (10 frames/s by default: the rings move 1.4 px a frame), a wave at
// 20 frames/s for its 1.8 s (its front moves 41 px a frame, within a glow
// 130 px wide). With `running` false the drift slows to a stop (Pace); once
// still, and with no wave in flight, the layer costs nothing.
//
// Geometry is in logical pixels of the 1920x1080 design; the item is opaque
// and meant to fill the window. GUI thread only (the shell uses the basic
// render loop, which syncs and renders on it).
#pragma once

#include "sceneclock.h"

#include <QColor>
#include <QElapsedTimer>
#include <QImage>
#include <QList>
#include <QPointF>
#include <QQuickItem>
#include <QtQml/qqmlregistration.h>

#include <array>

class Backdrop : public QQuickItem {
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(QPointF orb READ orb WRITE setOrb NOTIFY orbChanged)
    // Local time of day in hours, [0, 24): chooses the ambient light's colour
    // and strength.
    Q_PROPERTY(qreal hour READ hour WRITE setHour NOTIFY hourChanged)
    // A colour for the ambient light instead of the hour's (a Game Card's
    // [presentation] background or its Shape's light): its hue at the hour's
    // colour's luminance, its strength still the hour's.
    // Invalid (the default) or transparent: the hour's colour.
    Q_PROPERTY(QColor glowColor READ glowColor WRITE setGlowColor NOTIFY glowColorChanged)
    // A Game Card's MUN Shape tints the world modestly: the blue and patina
    // glows take this colour's hue at their own luminance and strength, so
    // the world is never lighter or darker than MUN draws it. Invalid (the
    // default): MUN's.
    Q_PROPERTY(QColor tint READ tint WRITE setTint NOTIFY tintChanged)
    // Whether the network drifts; waves run to their end either way.
    Q_PROPERTY(bool running READ running WRITE setRunning NOTIFY runningChanged)
    // Frames per second of the drift; 0 keeps the network still between waves.
    Q_PROPERTY(int driftRate READ driftRate WRITE setDriftRate NOTIFY driftRateChanged)

public:
    explicit Backdrop(QQuickItem *parent = nullptr);
    ~Backdrop() override;

    Q_INVOKABLE void ripple();

    QPointF orb() const { return m_orb; }
    void setOrb(const QPointF &orb);
    qreal hour() const { return m_hour; }
    void setHour(qreal hour);
    QColor glowColor() const { return m_glowColor; }
    void setGlowColor(const QColor &colour);
    QColor tint() const { return m_tint; }
    void setTint(const QColor &colour);
    bool running() const { return m_running; }
    void setRunning(bool running);
    int driftRate() const { return m_driftRate; }
    void setDriftRate(int rate);

signals:
    void orbChanged();
    void hourChanged();
    void glowColorChanged();
    void tintChanged();
    void runningChanged();
    void driftRateChanged();

protected:
    QSGNode *updatePaintNode(QSGNode *old, UpdatePaintNodeData *data) override;
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;

private:
    using Rgba = std::array<qreal, 4>;   // 0-255 channels and a 0-1 alpha, as the design's rgba()

    void onTick(quint64 frame);
    void updateDemand();
    int rate() const;
    Rgba glow() const;
    qreal wave(qreal radius) const;
    void repaint();
    void paintBase(const QSize &deviceSize, qreal scale);
    void paintFrame(QImage &frame, qreal scale) const;

    QPointF m_orb{470, 570};
    qreal m_hour = 12;
    QColor m_glowColor;
    QColor m_tint;
    QColor m_baseTint;         // the tint m_base was painted with
    bool m_running = true;
    int m_driftRate = 10;
    Pace m_pace;

    qreal m_time = 0;          // seconds of motion shown so far
    QElapsedTimer m_clock;     // since the last frame painted
    QList<qreal> m_ripples;    // each wave's progress, 0 to 1

    QImage m_base;             // the gradients, at device resolution
    Rgba m_baseGlow{};         // the ambient colour m_base was painted with
    std::array<QImage, 2> m_frames;   // painted in turns; the scene graph shows the last
    int m_nextFrame = 0;
    bool m_stale = true;       // the frame shown is out of date
};
