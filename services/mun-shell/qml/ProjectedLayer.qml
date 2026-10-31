import QtQuick
import QtQuick.Window
import MUN.Shell

// Shows its content through `projection`. How depends on the drawing path
// (src/renderinfo.h):
// - Qt Quick's software renderer: the content is laid out flat, out of
//   sight, and ProjectedView paints a projected copy of it. The software
//   renderer draws a transformed item through a per-pixel transform on every
//   frame that repaints its region, and Home repaints often (the wave, the
//   object's load); the copy costs a plain copy each frame and a projection
//   only when the content changes.
// - A graphics API (RenderInfo.rhi): the flat content itself is shown,
//   with the projection as its transform. It is rendered into a layer, a
//   texture of the flat content, so that it is composed as the copy was (an
//   opacity on it, or on an ancestor, applies to the whole, never to each
//   child over the others), and the GPU draws that texture through the
//   projection on every frame. The layer is rendered again only when the
//   content changes; there is no copy to refresh.
//
// On a graphics API the layer is sampled through the projection, which
// softens thin lines and small text a little (measured: a 2-pixel focus
// frame lost a tenth of its contrast). Content that never fades as a whole
// sets `layered` false: the GPU then draws it through the projection
// directly, every line and glyph at the display's own pixels, and there is
// no layer to keep or render again.
//
// The layer is the content's box; `margin` is room around it for what the
// content draws outside (shadows). `projection` maps the flat content's
// coordinates, margin included, to the view's. refresh() after a change;
// while `live` (a transition in progress) the copy is renewed every frame.
// Both are needed by the software path only.
Item {
    id: root
    property real margin: 0
    property matrix4x4 projection
    property bool live: false
    property bool layered: true
    default property alias flatItems: box.data
    readonly property bool gpu: RenderInfo.rhi
    // The projection as the copy draws it (QMatrix4x4::toTransform(): a
    // plane's projective map, x and y over w), with z kept at 0: a graphics
    // API clips what the full matrix would send outside its depth range,
    // and the design's perspectives do, while the plane's map has no depth.
    readonly property matrix4x4 planar: {
        const p = root.projection
        return Qt.matrix4x4(p.m11, p.m12, 0, p.m14,
                            p.m21, p.m22, 0, p.m24,
                            0, 0, 1, 0,
                            p.m41, p.m42, 0, p.m44)
    }

    function refresh() {
        if (!gpu)
            Qt.callLater(view.refresh)
    }
    // The content's point under `point` of the layer (for pointer input).
    function toContent(point) {
        if (gpu) {
            // Through the transform, back to the flat content.
            const flatPoint = flat.mapFromItem(root, point.x, point.y)
            return Qt.point(flatPoint.x - root.margin, flatPoint.y - root.margin)
        }
        const flatPoint = view.toSource(Qt.point(point.x + root.margin, point.y + root.margin))
        return Qt.point(flatPoint.x - root.margin, flatPoint.y - root.margin)
    }

    onWidthChanged: refresh()
    onHeightChanged: refresh()
    onGpuChanged: refresh()

    ProjectedView {
        id: view
        visible: !root.gpu
        x: -root.margin
        y: -root.margin
        width: flat.width
        height: flat.height
        source: root.gpu ? null : flat
        projection: root.projection
    }

    // At a device pixel ratio under 1 (720p) the software renderer's grab
    // keeps only that fraction of an item (see ProjectedView), so the flat
    // item is enlarged by its inverse and the content stays within the part
    // kept. Not on the graphics path, where nothing is grabbed.
    Item {
        id: flat
        readonly property real room: root.gpu ? 1 : 1 / Math.min(1, Screen.devicePixelRatio)
        x: -root.margin
        y: -root.margin
        width: (root.width + 2 * root.margin) * room
        height: (root.height + 2 * root.margin) * room
        opacity: root.gpu ? 1 : 0
        transform: Matrix4x4 { matrix: root.gpu ? root.planar : Qt.matrix4x4() }
        layer.enabled: root.gpu && root.layered
        layer.smooth: true
        Item {
            id: box
            x: root.margin
            y: root.margin
            width: root.width
            height: root.height
        }
    }

    FrameAnimation {
        running: !root.gpu && root.live && root.visible
        onTriggered: view.refresh()
    }
}
