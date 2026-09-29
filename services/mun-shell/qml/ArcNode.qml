import QtQuick
import MUN.Shell

// One entry of a menu arc: a knob, a wire and a bar with the label and an
// optional detail. Chosen (`on`), the knob's ring and dot take the accent
// (copper, or the active card's) and glow, the wire takes it too, and the
// bar turns moon-white with an accent edge and slides 4 px out. As in the
// design, the bar's background changes at once and its light and shadows
// over 0.3 s. `compact` is the Settings arc's smaller size.
//
// The entry is laid out flat and shown through its part of the arcs'
// perspective (ProjectedLayer), projected again only while it changes.
ProjectedLayer {
    id: root
    property string label
    property string detail
    property bool on: false
    property bool compact: false
    // The entry's corner in the arcs' plane and the arc's shift along it.
    property real originX
    property real originY
    property real shift
    signal clicked()

    // The arcs' perspective in the entry's own coordinates (Theme.lean), and
    // where it takes the entry's corner.
    readonly property matrix4x4 lean: Theme.lean(originX, originY, shift)
    readonly property real driftX: lean.m14 / lean.m44
    readonly property real driftY: lean.m24 / lean.m44
    // The layer stands on whole pixels where the projected corner falls, so
    // its copy is drawn as it is (a copy at a fraction of a pixel would be
    // resampled on every frame); the projection carries the rest.
    x: Math.round(originX + driftX)
    y: Math.round(originY + driftY)
    // Room for the bar's and the knob's shadows and glows.
    margin: 56
    projection: {
        const m = root.margin
        return Qt.matrix4x4(1, 0, 0, originX - x + m, 0, 1, 0, originY - y + m, 0, 0, 1, 0, 0, 0, 0, 1).times(root.lean)
                 .times(Qt.matrix4x4(1, 0, 0, -m, 0, 1, 0, -m, 0, 0, 1, 0, 0, 0, 0, 1))
    }
    // The transitions below last up to 0.35 s: the copy follows them frame
    // by frame, then rests.
    live: transition.running
    Timer { id: transition; interval: 450 }
    onOnChanged: transition.restart()
    onLabelChanged: refresh()
    onDetailChanged: refresh()
    onCompactChanged: refresh()
    readonly property color accent: Theme.accent
    onAccentChanged: transition.restart()

    MouseArea {
        parent: root
        anchors.fill: parent
        onClicked: root.clicked()
    }

    readonly property int knobSize: compact ? 28 : 34
    readonly property int wireWidth: compact ? 34 : 42
    readonly property int barHeight: compact ? 50 : 58
    readonly property int barMinWidth: compact ? 290 : 330
    readonly property int labelSize: compact ? 25 : 30
    readonly property var barRadii: [8, 30, 30, 8]

    width: knobSize + wireWidth + bar.width + 4
    height: barHeight
    Behavior on opacity { NumberAnimation { duration: 400; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }

    component Fade: NumberAnimation { duration: 300; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease }
    component Tint: ColorAnimation { duration: 300; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease }

    // The knob: a blue-black bead in a thin ring.
    Box {
        id: knob
        width: root.knobSize
        height: root.knobSize
        anchors.verticalCenter: parent.verticalCenter
        radii: [root.knobSize / 2]
        gradient: ({ type: "radial", at: [0.4, 0.35], stops: [[0, Theme.rgba(62, 92, 130, 0.35)], [0.75, Theme.rgba(14, 17, 23, 0.9)]] })

        Shadow {
            z: -1
            radii: [root.knobSize / 2]
            shadows: [{ y: 2, blur: 6, color: Theme.rgba(0, 0, 0, 0.45) }]
            opacity: root.on ? 0 : 1
            Behavior on opacity { Fade {} }
        }
        Shadow {
            z: -1
            radii: [root.knobSize / 2]
            shadows: [{ blur: 16, color: Theme.accentAlpha(0.3) }]
            opacity: root.on ? 1 : 0
            Behavior on opacity { Fade {} }
        }
        Box {
            anchors.fill: parent
            radii: [root.knobSize / 2]
            insets: [{ spread: 1, color: Theme.rgba(143, 176, 214, 0.22) }]
            opacity: root.on ? 0 : 1
            Behavior on opacity { Fade {} }
        }
        Box {
            anchors.fill: parent
            radii: [root.knobSize / 2]
            insets: [{ spread: 1.5, color: Theme.accent }]
            opacity: root.on ? 1 : 0
            Behavior on opacity { Fade {} }
        }
        Rectangle {
            anchors.centerIn: parent
            width: 9
            height: 9
            radius: 4.5
            color: root.on ? Theme.accent : Theme.rgba(143, 176, 214, 0.35)
            Behavior on color { Tint {} }
            Shadow {
                z: -1
                radii: [4.5]
                shadows: [{ blur: 8, spread: 1, color: Theme.accentAlpha(0.7) }]
                opacity: root.on ? 1 : 0
                Behavior on opacity { Fade {} }
            }
        }
    }

    // The wire to the bar; it changes colour at once.
    Rectangle {
        x: root.knobSize
        anchors.verticalCenter: parent.verticalCenter
        width: root.wireWidth
        height: 2
        radius: 1
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: root.on ? Theme.accentDeepAlpha(0.6) : Theme.rgba(143, 176, 214, 0.18) }
            GradientStop { position: 1; color: root.on ? Theme.accent : Theme.rgba(218, 215, 209, 0.28) }
        }
    }

    Item {
        id: bar
        x: root.knobSize + root.wireWidth + (root.on ? 4 : 0)
        width: Math.max(root.barMinWidth, 26 + label.implicitWidth + 20 + detail.implicitWidth + 26)
        height: root.barHeight
        Behavior on x { NumberAnimation { duration: 350; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.settle } }

        Shadow {
            z: -1
            radii: root.barRadii
            shadows: [{ y: 8, blur: 18, spread: -10, color: Theme.rgba(0, 0, 0, 0.7) }]
            opacity: root.on ? 0 : 1
            Behavior on opacity { Fade {} }
        }
        Shadow {
            z: -1
            radii: root.barRadii
            shadows: [
                { y: 2, color: Theme.rgba(75, 53, 37, 0.55) },
                { y: 12, blur: 26, spread: -12, color: Theme.rgba(0, 0, 0, 0.75) },
                { blur: 30, spread: -8, color: Theme.accentAlpha(0.25) }
            ]
            opacity: root.on ? 1 : 0
            Behavior on opacity { Fade {} }
        }
        Box {
            anchors.fill: parent
            visible: !root.on
            radii: root.barRadii
            gradient: ({ type: "linear", angle: 160, stops: [[0, Theme.rgba(62, 92, 130, 0.16)], [0.6, Theme.rgba(20, 24, 32, 0.42)], [1, Theme.rgba(10, 12, 16, 0.5)]] })
        }
        Box {
            anchors.fill: parent
            visible: root.on
            radii: root.barRadii
            gradient: ({ type: "linear", angle: 180, stops: [[0, "#E8E5DF"], [1, "#CCC9C3"]] })
        }
        Box {
            anchors.fill: parent
            radii: root.barRadii
            insets: [{ y: 1, color: Theme.rgba(255, 255, 255, 0.06) }, { spread: 1, color: Theme.rgba(143, 176, 214, 0.14) }]
            opacity: root.on ? 0 : 1
            Behavior on opacity { Fade {} }
        }
        Box {
            anchors.fill: parent
            radii: root.barRadii
            insets: [{ x: 4, color: Theme.accentDeep }, { y: 1, color: Theme.rgba(255, 255, 255, 0.7) }]
            opacity: root.on ? 1 : 0
            Behavior on opacity { Fade {} }
        }

        UiText {
            id: label
            x: 26
            anchors.verticalCenter: parent.verticalCenter
            text: root.label
            size: root.labelSize
            weight: 700
            stretch: 118
            tracking: 0.01
            color: root.on ? Theme.inkOnMoon : Theme.moon
            Behavior on color { Tint {} }
        }
        UiText {
            id: detail
            anchors.right: parent.right
            anchors.rightMargin: 26
            anchors.verticalCenter: parent.verticalCenter
            text: root.detail
            size: 19
            weight: 500
            color: root.on ? Theme.ashOnMoon : Theme.ash
            Behavior on color { Tint {} }
        }
    }

}
