import QtQuick
import QtQuick.Window
import MUN.Shell

// Shows its content through `projection`: the content is laid out flat, out
// of sight, and ProjectedView paints a projected copy of it. The software
// renderer draws a transformed item through a per-pixel transform on every
// frame that repaints its region, and Home repaints often (the wave, the
// object's load); the copy costs a plain copy each frame and a projection
// only when the content changes.
//
// The layer is the content's box; `margin` is room around it for what the
// content draws outside (shadows). `projection` maps the flat content's
// coordinates, margin included, to the view's. refresh() after a change;
// while `live` (a transition in progress) the copy is renewed every frame.
Item {
    id: root
    property real margin: 0
    property matrix4x4 projection
    property bool live: false
    default property alias flatItems: box.data

    function refresh() { Qt.callLater(view.refresh) }
    // The content's point under `point` of the layer (for pointer input).
    function toContent(point) {
        const flatPoint = view.toSource(Qt.point(point.x + root.margin, point.y + root.margin))
        return Qt.point(flatPoint.x - root.margin, flatPoint.y - root.margin)
    }

    onWidthChanged: refresh()
    onHeightChanged: refresh()

    ProjectedView {
        id: view
        x: -root.margin
        y: -root.margin
        width: flat.width
        height: flat.height
        source: flat
        projection: root.projection
    }

    // At a device pixel ratio under 1 (720p) the software renderer's grab
    // keeps only that fraction of an item (see ProjectedView), so the flat
    // item is enlarged by its inverse and the content stays within the part
    // kept.
    Item {
        id: flat
        readonly property real room: 1 / Math.min(1, Screen.devicePixelRatio)
        x: -root.margin
        y: -root.margin
        width: (root.width + 2 * root.margin) * room
        height: (root.height + 2 * root.margin) * room
        opacity: 0
        Item {
            id: box
            x: root.margin
            y: root.margin
            width: root.width
            height: root.height
        }
    }

    FrameAnimation {
        running: root.live && root.visible
        onTriggered: view.refresh()
    }
}
