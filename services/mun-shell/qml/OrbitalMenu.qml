import QtQuick
import MUN.Shell

// The orbital design's menu (Home on a graphics API): `entries` ({key, label,
// detail, icon}) as OrbitalNode pills, one under another about the orbit's
// centre, 104 px apart (76 compact: Settings' sections), each with its node
// where the orbit's node ring crosses its height and every pill from the
// same edge. Hidden, it fades and moves 60 px left; dimmed (the panel's
// options have the focus), its entries step back (OrbitalNode); once faded
// out, it is no longer drawn. Its icons rest: the one in focus moves, large,
// at the orbit's centre (OrbitalFocus).
Item {
    id: root
    property var entries: []
    property int current: 0
    property bool compact: false
    property bool hidden: false
    property bool dimmed: false
    property bool dressed: false
    // A pointer chose entry `index`.
    signal activated(int index)

    readonly property real spacing: compact ? 76 : 104
    enabled: !hidden
    opacity: hidden ? 0 : 1
    visible: opacity > 0
    Behavior on opacity { NumberAnimation { duration: 450; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }
    property real shift: hidden ? -60 : 0
    Behavior on shift { NumberAnimation { duration: 600; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.settle } }

    Repeater {
        model: root.entries.length
        OrbitalNode {
            required property int index
            readonly property var entry: root.entries[index] || ({})
            readonly property real dy: (index - (root.entries.length - 1) / 2) * root.spacing
            x: root.shift
            centreY: Theme.orbitalCentre.y + dy
            nodeX: Theme.orbitalCentre.x + Math.sqrt(Math.max(0, Theme.orbitalNodeRing * Theme.orbitalNodeRing - dy * dy))
            pillX: Theme.orbitalMenuX
            label: entry.label || ""
            detail: entry.detail || ""
            icon: entry.icon || ""
            tint: Theme.orbitalAccent(root.compact ? "settings" : entry.key)
            tintLight: Theme.orbitalAccentLight(root.compact ? "settings" : entry.key)
            on: index === root.current
            compact: root.compact
            dressed: root.dressed
            dimmed: root.dimmed
            onClicked: root.activated(index)
        }
    }
}
