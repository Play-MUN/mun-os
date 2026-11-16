// Home's network of light on the graphics path (src/renderinfo.h): Backdrop's
// rings, spokes, the stretches of them a wave lights, the dashed orbits and
// the floor (backdrop.h) as one geometry that the GPU places on every frame.
//
// The geometry is built once for an orb, a size and a device pixel ratio;
// a frame then only sets a few numbers in the material (Frame): how far the
// rings have drifted and the spokes turned, the waves' phase, the floor's,
// the dashes' offsets and the waves in flight. The vertex shader
// (shaders/network.vert) places every vertex from them as
// Backdrop::paintFrame() places its lines, and draws each line as hairline.h
// does: the vertex on the line carries its alpha, the two beside it none, a
// device pixel away along its minor axis, and the GPU interpolates. The CPU
// does no per-frame geometry and uploads no image: a frame costs it the
// numbers and a draw call.
//
// Up to eight waves at once: a ninth, sent while eight are still in flight,
// takes the place of the oldest (the faintest). The raster path has no such
// limit; at most one wave a key press, eight need more than four presses a
// second for two seconds.
//
// Render thread only, as any scene-graph node (Backdrop::updatePaintNode).
#pragma once

#include <QPointF>
#include <QSGGeometryNode>
#include <QSizeF>

#include <array>

class NetworkNode : public QSGGeometryNode {
public:
    static constexpr int kWaves = 8;

    // What moves, at one frame. Angles in radians; lengths in item units.
    struct Frame {
        qreal drift = 0;    // the rings' drift, 0 to 150
        qreal turn = 0;     // the spokes'
        qreal wobble = 0;   // the phase of a wave's ripple along the rings
        qreal floor = 0;    // the floor's slide, 0 to 1
        std::array<qreal, 2> dash{};   // the orbits' dash offsets, 0 to 12
        // Each wave's front (its radius) and strength (1 - its progress);
        // strength 0: none.
        std::array<QPointF, kWaves> waves{};
    };

    NetworkNode();

    // Builds the geometry again if any of these changed. `pixel` is one
    // device pixel in item units.
    void setShape(const QPointF &orb, const QSizeF &size, qreal pixel);
    void setFrame(const Frame &frame);

private:
    void build();

    QPointF m_orb;
    QSizeF m_size;
    qreal m_pixel = 0;
};
