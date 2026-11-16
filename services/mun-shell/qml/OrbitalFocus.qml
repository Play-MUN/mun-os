import QtQuick
import MUN.Shell

// The entry in focus, large at the orbit's centre where its axes cross (the
// orbital design): its icon (OrbitalIcon) in cream, with a finer stroke,
// over a soft disc of the night that keeps the axes from running through it.
// When the focus moves the icon there shrinks a little and fades, and the new
// one grows into place playing its gesture (OrbitalIcon.play), drawn on every
// frame while it runs; then it rests. The icon and its disc are hidden while
// a game's world covers the background (`overNight` false).
//
// It is also the card object (MUN Shape, docs/shape.md, "card"): while MUN
// Disk is in focus and the card's package gives a window, the window on the
// disc in the card's outline and light (OrbitalCard), over MUN's night or
// the game's world, coming in as the transition reaches the object
// (`cardReveal`) while MUN's icon gives way to it. Without a window, or with
// one that did not decode, MUN's icon stays whole.
Item {
    id: root
    property string icon
    // Its colour, the entry's light: it changes with the icon, while the
    // icon is out of sight, so its parts are painted again only then.
    property color tint: Theme.orbitalCream
    property bool overNight: true
    property bool moving: !ShellSettings.reduceMotion
    // MUN's icon and its disc shown, eased: 1 over MUN's night.
    property real night: overNight ? 1 : 0
    Behavior on night { enabled: root.moving; NumberAnimation { duration: 450; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }
    // The card object's MUN Shape (Shape's window, cardShape, morph, glow and
    // objectProgress).
    property var cardWindow
    property string cardShape: "card"
    property real morph: 0
    property color cardGlow: "transparent"
    property real cardReveal: 0

    readonly property int disc: 236
    width: disc + 120
    height: disc + 120
    x: Theme.orbitalCentre.x - width / 2
    y: Theme.orbitalCentre.y - height / 2
    visible: glyph.name !== ""

    Box {
        anchors.centerIn: parent
        width: root.disc
        height: root.disc
        radii: [root.disc / 2]
        color: Theme.rgba(22, 18, 15, 0.9)
        opacity: root.night
        visible: opacity > 0
        Shadow {
            z: -1
            radii: [root.disc / 2]
            shadows: [{ blur: 48, color: Theme.rgba(22, 18, 15, 0.9) }]
        }
    }

    // What the focus shows, swapped as a whole: MUN's icon and, for MUN
    // Disk, the card object over it.
    Item {
        id: face
        anchors.fill: parent
        // How far the card's object has replaced MUN's icon.
        readonly property real carded: glyph.name === "disk" && card.hasWindow ? root.cardReveal : 0

        OrbitalIcon {
            id: glyph
            anchors.centerIn: parent
            size: 168
            stroke: 0.6
            opacity: root.night * (1 - face.carded)
            visible: opacity > 0
        }
        OrbitalCard {
            id: card
            anchors.fill: parent
            visible: glyph.name === "disk"
            cardWindow: root.cardWindow
            cardShape: root.cardShape
            morph: root.morph
            cardGlow: root.cardGlow
            cardReveal: root.cardReveal
        }
    }

    Component.onCompleted: {
        glyph.colour = tint
        glyph.name = icon
        glyph.play()
    }
    onIconChanged: {
        if (!moving || glyph.name === "") {
            swap.stop()
            face.opacity = 1
            face.scale = 1
            glyph.colour = tint
            glyph.name = icon
            return
        }
        swap.restart()
    }
    SequentialAnimation {
        id: swap
        ParallelAnimation {
            NumberAnimation { target: face; property: "opacity"; to: 0; duration: 140; easing.type: Easing.InCubic }
            NumberAnimation { target: face; property: "scale"; to: 0.9; duration: 140; easing.type: Easing.InCubic }
        }
        ScriptAction { script: { glyph.colour = root.tint; glyph.name = root.icon; glyph.play() } }
        ParallelAnimation {
            NumberAnimation { target: face; property: "opacity"; to: 1; duration: 260; easing.type: Easing.OutCubic }
            NumberAnimation { target: face; property: "scale"; from: 0.9; to: 1; duration: 260; easing.type: Easing.OutCubic }
        }
    }
}
