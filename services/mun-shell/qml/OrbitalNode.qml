import QtQuick
import MUN.Shell

// One entry of the orbital menu (OrbitalMenu): a node on the orbit's ring,
// a connector and a pill with an icon and the label. Chosen (`on`), the
// node's ring and dot turn cream and glow, the pill fills with cream, grows
// to the right (never over its node) and shows its detail after a thin rule
// instead of its icon. Flat: no perspective. `compact` is the Settings
// list's smaller size.
//
// A `dressed` entry (the main menu's) takes the active card's identity as
// ArcNode's does (Shape, docs/shape.md): when the transition's front reaches
// its pill (`reached`), its plate blends by the plan the checker proved
// (Shape.blend), its text and focus change at one point, and a chosen pill
// becomes a bar of the game's text colour with the plate's colour as label.
// Dimmed (the panel's options have the focus), MUN's entry fades as a whole
// to 0.4; a dressed one keeps its proven plate and text whole and fades only
// its node and connector.
Item {
    id: root
    property string label
    property string detail
    property string icon
    property bool on: false
    property bool compact: false
    property bool dressed: false
    property bool dimmed: false
    property bool moving: !ShellSettings.reduceMotion
    // The node's centre (on the ring) and where the pills begin, in the canvas.
    property real nodeX
    property real centreY
    property real pillX
    signal clicked()

    readonly property int pillHeight: compact ? 54 : 66
    readonly property int pillWidth: compact ? 330 : 370
    readonly property int grow: 22
    readonly property int labelSize: compact ? 24 : 28
    x: 0
    y: 0
    width: pillX + pillWidth + grow
    height: Theme.canvasHeight

    // How far the transition has reached the pill, and what it shows there.
    readonly property rect box: Qt.rect(pillX, centreY - pillHeight / 2, pill.width, pillHeight)
    readonly property real reached: dressed && Shape.plated ? Shape.reach(Shape.phase, Shape.kind, Shape.progress, box) : 0
    readonly property bool shaped: reached > 0
    readonly property var look: shaped ? Shape.blend("entries", reached) : ({ plate: Shape.plate, opacity: 1, material: "solid", amount: 0, game: false })
    readonly property var barLook: shaped ? Shape.blend("bar", reached) : ({ plate: Shape.bar, game: false })
    readonly property bool faded: dimmed && shaped
    readonly property bool lit: on && !faded
    readonly property real ornaments: faded ? 0.4 : 1
    // A lent or read accent only while a card lends one; MUN's cream otherwise.
    readonly property bool gameFocus: shaped ? look.game : dressed && !Shape.plated && Shape.source !== "none"
    // The entry's own light (Theme.orbitalAccents).
    property color tint: Theme.orbitalCream
    readonly property color accent: gameFocus ? Shape.focus : root.tint
    readonly property color labelColour: shaped ? (lit ? (barLook.game ? Shape.barText : Theme.inkOnMoon) : (look.game ? Shape.text : Theme.moon))
                                                : (on ? Theme.orbitalInk : Theme.orbitalText)
    readonly property color detailColour: shaped ? labelColour : (on ? Theme.orbitalInkSoft : Theme.orbitalTextDim)

    // MUN's fade while the options have the focus; a dressed entry stays whole.
    opacity: dimmed && !shaped ? 0.4 : 1
    Behavior on opacity { enabled: root.moving; NumberAnimation { duration: 400; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }

    component Fade: NumberAnimation { duration: 200; easing.type: Easing.OutCubic }

    // The node on the ring, and its connector to the pill.
    Item {
        id: node
        x: root.nodeX - width / 2
        y: root.centreY - height / 2
        width: 30
        height: 30
        opacity: root.ornaments
        Rectangle {
            anchors.centerIn: parent
            width: root.on ? 30 : 18
            height: width
            radius: width / 2
            color: "transparent"
            border.width: root.on ? 2 : 1
            border.color: root.on ? root.accent : Theme.rgba(255, 255, 255, 0.22)
            Behavior on width { enabled: root.moving; Fade {} }
        }
        Rectangle {
            anchors.centerIn: parent
            width: root.on ? 12 : 8
            height: width
            radius: width / 2
            color: root.on ? root.accent : Theme.rgba(217, 209, 199, 0.7)
            Behavior on width { enabled: root.moving; Fade {} }
            Shadow {
                z: -1
                radii: [6]
                shadows: [{ blur: 14, spread: 2, color: Theme.alpha(root.accent, 0.55) }]
                opacity: root.on ? 1 : 0
                Behavior on opacity { enabled: root.moving; Fade {} }
            }
        }
    }
    Rectangle {
        x: root.nodeX + (root.on ? 15 : 9)
        y: root.centreY - 0.75
        width: Math.max(0, root.pillX - x)
        height: 1.5
        opacity: root.ornaments
        color: root.on ? Theme.alpha(root.accent, 0.7) : Theme.rgba(255, 255, 255, 0.18)
    }

    Item {
        id: pill
        x: root.pillX
        y: root.centreY - height / 2
        // At least its size (grown to the right when chosen), wider when its
        // content needs it: the icon and label, or chosen, the label, a rule
        // and the detail.
        readonly property real restWidth: Math.max(root.pillWidth, (root.icon === "" ? 30 : (root.compact ? 72 : 86)) + labelText.implicitWidth + 34)
        readonly property real chosenWidth: Math.max(root.pillWidth + root.grow,
                                                     30 + labelText.implicitWidth + (root.detail !== "" ? 37.5 + detailText.implicitWidth : 0) + 34)
        width: root.on ? chosenWidth : restWidth
        height: root.pillHeight
        Behavior on width { enabled: root.moving; NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

        Shadow {
            z: -1
            radii: [root.pillHeight / 2]
            shadows: [{ y: 8, blur: 20, spread: -8, color: Theme.rgba(0, 0, 0, 0.55) }]
            opacity: root.lit ? 0 : 1
            Behavior on opacity { enabled: root.moving; Fade {} }
        }
        Shadow {
            z: -1
            radii: [root.pillHeight / 2]
            shadows: [{ blur: 34, color: Theme.alpha(root.accent, 0.28) }, { y: 10, blur: 24, spread: -8, color: Theme.rgba(0, 0, 0, 0.6) }]
            opacity: root.lit ? 1 : 0
            Behavior on opacity { enabled: root.moving; Fade {} }
        }
        // MUN's glass, and the cream of the focus over it.
        Box {
            anchors.fill: parent
            visible: !root.shaped
            radii: [root.pillHeight / 2]
            color: Theme.orbitalGlass
            insets: [{ spread: 1, color: Theme.rgba(255, 255, 255, 0.13) }, { y: 1, color: Theme.rgba(255, 255, 255, 0.05) }]
        }
        Box {
            anchors.fill: parent
            visible: !root.shaped
            radii: [root.pillHeight / 2]
            opacity: root.on ? 1 : 0
            gradient: ({ type: "linear", angle: 90, stops: [[0, Theme.mixColour(root.tint, Theme.orbitalCream, 0.45)], [1, root.tint]] })
            insets: [{ y: 1, color: Theme.rgba(255, 255, 255, 0.6) }]
            Behavior on opacity { enabled: root.moving; Fade {} }
        }
        // The game's: its plate at its proven opacity; chosen, its bar.
        Material {
            anchors.fill: parent
            visible: root.shaped
            radii: [root.pillHeight / 2]
            plate: root.lit ? root.barLook.plate : root.look.plate
            opacity: root.lit ? 1 : root.look.opacity
            material: root.lit ? "solid" : root.look.material
            amount: root.lit ? 1 : root.look.amount
        }
        Box {
            anchors.fill: parent
            visible: root.shaped
            radii: [root.pillHeight / 2]
            insets: [{ spread: 1.5, color: root.accent }]
            opacity: root.on ? root.ornaments : 0
        }

        OrbitalIcon {
            x: root.compact ? 26 : 32
            anchors.verticalCenter: parent.verticalCenter
            size: root.compact ? 28 : 34
            name: root.icon
            colour: root.labelColour
            opacity: root.on || root.icon === "" ? 0 : 1
            Behavior on opacity { enabled: root.moving; NumberAnimation { duration: 160 } }
        }
        Row {
            x: root.on || root.icon === "" ? 30 : (root.compact ? 72 : 86)
            anchors.verticalCenter: parent.verticalCenter
            spacing: 18
            Behavior on x { enabled: root.moving; NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
            UiText {
                id: labelText
                anchors.verticalCenter: parent.verticalCenter
                text: root.label
                size: root.labelSize
                weight: root.on ? 700 : 600
                color: root.labelColour
                Behavior on color { enabled: root.moving && !root.shaped; ColorAnimation { duration: 200 } }
            }
            Rectangle {
                visible: root.on && root.detail !== ""
                anchors.verticalCenter: parent.verticalCenter
                width: 1.5
                height: root.compact ? 18 : 22
                color: Theme.alpha(root.detailColour, 0.5)
            }
            UiText {
                id: detailText
                visible: root.on && root.detail !== ""
                anchors.verticalCenter: parent.verticalCenter
                text: root.detail
                size: root.compact ? 17 : 19
                color: root.detailColour
            }
        }
        MouseArea {
            anchors.fill: parent
            onClicked: root.clicked()
        }
    }
}
