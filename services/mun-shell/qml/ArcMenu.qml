import QtQuick
import MUN.Shell

// A menu arc around the orb: `entries` ({label, detail}) spread `spread`
// degrees apart on a circle of `radius`, leaning back into the scene with
// the arcs' perspective (Theme.lean). Hidden, it fades and moves 60 px
// left; dimmed (while its panel's options have the focus), its entries fade
// to 0.4. The delegates stay while the entries' texts change, so their
// transitions run.
Item {
    id: root
    property var entries: []
    property int current: 0
    property real radius: 340
    property real spread: 24
    property bool compact: false
    property bool hidden: false
    property bool dimmed: false
    // The main arc's entries take the active Game Card's identity (ArcNode).
    property bool dressed: false
    // A pointer chose entry `index`.
    signal activated(int index)

    enabled: !hidden
    opacity: hidden ? 0 : 1
    Behavior on opacity { NumberAnimation { duration: 450; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }
    // Hidden, the arc moves 60 px left in its leaning plane.
    property real shift: hidden ? -60 : 0
    Behavior on shift { NumberAnimation { duration: 600; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.settle } }

    Repeater {
        model: root.entries.length
        ArcNode {
            required property int index
            readonly property real angle: (index - (root.entries.length - 1) / 2) * root.spread * Math.PI / 180
            // The design places every node by the main arc's knob (34) and bar (58).
            originX: Theme.orb.x + Math.cos(angle) * root.radius - 17
            originY: Theme.orb.y + Math.sin(angle) * root.radius - 29
            shift: root.shift
            label: root.entries[index] ? root.entries[index].label : ""
            detail: root.entries[index] ? (root.entries[index].detail || "") : ""
            on: index === root.current
            compact: root.compact
            dressed: root.dressed
            opacity: root.dimmed ? 0.4 : 1
            onClicked: root.activated(index)
        }
    }
}
