import QtQuick
import MUN.Shell

// One entry of a menu arc: a knob, a wire and a bar with the label and an
// optional detail. Chosen (`on`), the knob's ring and dot take the accent
// (copper, or the active card's) and glow, the wire takes it too, and the
// bar turns moon-white with an accent edge and slides 4 px out. As in the
// design, the bar's background changes at once and its light and shadows
// over 0.3 s. `compact` is the Settings arc's smaller size.
//
// A `dressed` entry (the main arc's) takes the active Game Card's identity
// (Shape, docs/shape.md): its focus colour, and, while the card's colours
// hold, the game's plate under its label (at the opacity the contrast rule
// gives, in the package's material), its text colour, and a chosen bar of
// the text's colour with the plate's colour as label; where the game's
// colours do not hold but its world is drawn, MUN's own set on plates of
// their proven opacity. Every other entry, the Settings arc's among them,
// stays MUN's.
//
// The identity arrives and leaves with a transition: the entry takes it when
// the transition's front reaches it (`reached`, 0 to 1, from Shape.reach),
// its plate blending by the plan the checker proved (Shape.blend), its text
// and focus changing at one point of it.
//
// `dimmed` (while the panel's options have the focus): MUN's entry fades as
// a whole to 0.4. A dressed one never fades its plate or its text, whose
// contrast is proven only as they are drawn, not under a further opacity:
// they are whole from the first frame it is dressed (it never eases out of
// MUN's fade into its identity), its knob and wire fade instead, and its
// chosen bar turns back into a plate, marked only by its place 4 px out and
// a faded focus edge, so the panel's option is the only focus on screen.
// Turned back into MUN's while dimmed, it eases from whole to MUN's fade.
//
// The entry is laid out flat and shown through its part of the arcs'
// perspective (ProjectedLayer), projected again only while it changes.
ProjectedLayer {
    id: root
    property string label
    property string detail
    property bool on: false
    property bool compact: false
    property bool dressed: false
    property bool dimmed: false
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
    // by frame, then rests. A change of identity is one step (a cut).
    live: transition.running
    Timer { id: transition; interval: 450 }
    onOnChanged: transition.restart()
    onLabelChanged: refresh()
    onDetailChanged: refresh()
    onCompactChanged: refresh()
    onDimmedChanged: {
        transition.restart()
        settleFade(true)
    }

    // How far the transition has reached the entry's bar (canvas coordinates),
    // and what it shows there: MUN's look at 0, a plate from just past it.
    readonly property rect box: Qt.rect(originX + knobSize + wireWidth, originY, bar.width, barHeight)
    readonly property real reached: dressed && Shape.plated ? Shape.reach(Shape.phase, Shape.kind, Shape.progress, box) : 0
    readonly property bool shaped: reached > 0
    readonly property var look: shaped ? Shape.blend("entries", reached) : ({ plate: Shape.plate, opacity: 1, material: "solid", amount: 0, game: false })
    readonly property var barLook: shaped ? Shape.blend("bar", reached) : ({ plate: Shape.bar, game: false })
    onReachedChanged: transition.restart()
    // Dimmed and dressed: the entry keeps its proven pair and fades only its
    // ornaments; its chosen bar (`lit`) is shown only while not dimmed.
    readonly property bool faded: dimmed && shaped
    readonly property bool lit: on && !faded
    readonly property real ornaments: faded ? 0.4 : 1
    // The focus: the game's with its set, MUN's copper before it (or a lent
    // or cover-read accent, which dresses no plate and shows at once).
    readonly property bool gameFocus: shaped ? look.game : dressed && !Shape.plated
    readonly property color accent: gameFocus ? Shape.focus : Theme.accent
    readonly property color accentDeep: gameFocus ? Shape.focusDeep : Theme.accentDeep
    function accentAlpha(a) { return Theme.alpha(root.accent, a) }
    function accentDeepAlpha(a) { return Theme.alpha(root.accentDeep, a) }
    // On a plate: the game's text once its plan says so, MUN's before (the
    // neutral set: moon on its plate, ink on its bar).
    readonly property color labelColour: shaped ? (lit ? (barLook.game ? Shape.barText : Theme.inkOnMoon) : (look.game ? Shape.text : Theme.moon))
                                                : (on ? Theme.inkOnMoon : Theme.moon)
    readonly property color detailColour: shaped ? labelColour : (on ? Theme.ashOnMoon : Theme.ash)
    onAccentChanged: transition.restart()
    onShapedChanged: {
        transition.restart()
        settleFade(!shaped)
    }
    Connections {
        target: Shape
        enabled: root.dressed
        function onChanged() { transition.restart() }
    }

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
    // MUN's fade (see `dimmed`): eased while the entry is MUN's, held at 1
    // while it is dressed, so a dressed entry's opacity is 1 on every frame.
    property real fade: 1
    opacity: shaped ? 1 : fade
    NumberAnimation {
        id: fading
        target: root
        property: "fade"
        duration: 400
        easing.type: Easing.BezierSpline
        easing.bezierCurve: Theme.ease
    }
    function settleFade(eased) {
        fading.stop()
        const to = dimmed && !shaped ? 0.4 : 1
        if (eased && to !== fade) {
            fading.to = to
            fading.start()
        } else {
            fade = to
        }
    }
    Component.onCompleted: settleFade(false)

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
        opacity: root.ornaments
        Behavior on opacity { Fade {} }

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
            shadows: [{ blur: 16, color: root.accentAlpha(0.3) }]
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
            insets: [{ spread: 1.5, color: root.accent }]
            opacity: root.on ? 1 : 0
            Behavior on opacity { Fade {} }
        }
        Rectangle {
            anchors.centerIn: parent
            width: 9
            height: 9
            radius: 4.5
            color: root.on ? root.accent : Theme.rgba(143, 176, 214, 0.35)
            Behavior on color { Tint {} }
            Shadow {
                z: -1
                radii: [4.5]
                shadows: [{ blur: 8, spread: 1, color: root.accentAlpha(0.7) }]
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
        opacity: root.ornaments
        Behavior on opacity { Fade {} }
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: root.on ? root.accentDeepAlpha(0.6) : Theme.rgba(143, 176, 214, 0.18) }
            GradientStop { position: 1; color: root.on ? root.accent : Theme.rgba(218, 215, 209, 0.28) }
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
            opacity: root.lit ? 0 : 1
            Behavior on opacity { Fade {} }
        }
        Shadow {
            z: -1
            radii: root.barRadii
            shadows: [
                { y: 2, color: Theme.rgba(75, 53, 37, 0.55) },
                { y: 12, blur: 26, spread: -12, color: Theme.rgba(0, 0, 0, 0.75) },
                { blur: 30, spread: -8, color: root.accentAlpha(0.25) }
            ]
            opacity: root.lit ? 1 : 0
            Behavior on opacity { Fade {} }
        }
        // MUN's bars.
        Box {
            anchors.fill: parent
            visible: !root.on && !root.shaped
            radii: root.barRadii
            gradient: ({ type: "linear", angle: 160, stops: [[0, Theme.rgba(62, 92, 130, 0.16)], [0.6, Theme.rgba(20, 24, 32, 0.42)], [1, Theme.rgba(10, 12, 16, 0.5)]] })
        }
        Box {
            anchors.fill: parent
            visible: root.on && !root.shaped
            radii: root.barRadii
            gradient: ({ type: "linear", angle: 180, stops: [[0, "#E8E5DF"], [1, "#CCC9C3"]] })
        }
        Box {
            anchors.fill: parent
            visible: !root.shaped
            radii: root.barRadii
            insets: [{ y: 1, color: Theme.rgba(255, 255, 255, 0.06) }, { spread: 1, color: Theme.rgba(143, 176, 214, 0.14) }]
            opacity: root.on ? 0 : 1
            Behavior on opacity { Fade {} }
        }
        // The game's: its plate, in its material, at the opacity its text
        // needs over any world; chosen, a bar of its text's colour.
        Material {
            anchors.fill: parent
            visible: root.shaped
            radii: root.barRadii
            plate: root.lit ? root.barLook.plate : root.look.plate
            opacity: root.lit ? 1 : root.look.opacity
            material: root.lit ? "solid" : root.look.material
            amount: root.lit ? 1 : root.look.amount
        }
        Box {
            anchors.fill: parent
            radii: root.barRadii
            insets: [{ x: 4, color: root.shaped ? root.accent : root.accentDeep }, { y: 1, color: Theme.rgba(255, 255, 255, root.shaped ? 0.06 : 0.7) }]
            opacity: root.on ? root.ornaments : 0
            Behavior on opacity { Fade {} }
        }

        // The label and detail in MUN's colours, which ease as the choice
        // changes; dressed, in the game's, which change in one step: text
        // over a game's plate switches colour at one point, never passing
        // through colours the contrast rule has not proven (docs/shape.md).
        UiText {
            id: label
            x: 26
            anchors.verticalCenter: parent.verticalCenter
            visible: !root.shaped
            text: root.label
            size: root.labelSize
            weight: 700
            stretch: 118
            tracking: 0.01
            color: root.on ? Theme.inkOnMoon : Theme.moon
            Behavior on color { Tint {} }
        }
        UiText {
            anchors.fill: label
            visible: root.shaped
            text: root.label
            size: root.labelSize
            weight: 700
            stretch: 118
            tracking: 0.01
            color: root.labelColour
        }
        UiText {
            id: detail
            anchors.right: parent.right
            anchors.rightMargin: 26
            anchors.verticalCenter: parent.verticalCenter
            visible: !root.shaped
            text: root.detail
            size: 19
            weight: 500
            color: root.on ? Theme.ashOnMoon : Theme.ash
            Behavior on color { Tint {} }
        }
        UiText {
            anchors.fill: detail
            visible: root.shaped
            text: root.detail
            size: 19
            weight: 500
            color: root.detailColour
        }
    }

}
