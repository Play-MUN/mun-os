import QtQuick
import MUN.Shell

// The entry in focus, large at the orbit's centre where its axes cross (the
// orbital design): its icon (OrbitalIcon) in cream, with a finer stroke,
// over a soft disc of the night that keeps the axes from running through it.
// When the focus moves the icon there shrinks a little and fades, and the new
// one grows into place playing its gesture (OrbitalIcon.play), drawn on every
// frame while it runs; then it rests. Hidden while a game's world covers the
// background.
Item {
    id: root
    property string icon
    // Its colour, the entry's light: it changes with the icon, while the
    // icon is out of sight, so its parts are painted again only then.
    property color tint: Theme.orbitalCream
    property bool shown: true
    property bool moving: !ShellSettings.reduceMotion

    readonly property int disc: 236
    width: disc + 120
    height: disc + 120
    x: Theme.orbitalCentre.x - width / 2
    y: Theme.orbitalCentre.y - height / 2
    opacity: shown && glyph.name !== "" ? 1 : 0
    visible: opacity > 0
    Behavior on opacity { enabled: root.moving; NumberAnimation { duration: 450; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }

    Box {
        anchors.centerIn: parent
        width: root.disc
        height: root.disc
        radii: [root.disc / 2]
        color: Theme.rgba(22, 18, 15, 0.9)
        Shadow {
            z: -1
            radii: [root.disc / 2]
            shadows: [{ blur: 48, color: Theme.rgba(22, 18, 15, 0.9) }]
        }
    }

    OrbitalIcon {
        id: glyph
        anchors.centerIn: parent
        size: 168
        stroke: 0.6
    }

    Component.onCompleted: {
        glyph.colour = tint
        glyph.name = icon
        glyph.play()
    }
    onIconChanged: {
        if (!moving || glyph.name === "") {
            swap.stop()
            glyph.opacity = 1
            glyph.scale = 1
            glyph.colour = tint
            glyph.name = icon
            return
        }
        swap.restart()
    }
    SequentialAnimation {
        id: swap
        ParallelAnimation {
            NumberAnimation { target: glyph; property: "opacity"; to: 0; duration: 140; easing.type: Easing.InCubic }
            NumberAnimation { target: glyph; property: "scale"; to: 0.9; duration: 140; easing.type: Easing.InCubic }
        }
        ScriptAction { script: { glyph.colour = root.tint; glyph.name = root.icon; glyph.play() } }
        ParallelAnimation {
            NumberAnimation { target: glyph; property: "opacity"; to: 1; duration: 260; easing.type: Easing.OutCubic }
            NumberAnimation { target: glyph; property: "scale"; from: 0.9; to: 1; duration: 260; easing.type: Easing.OutCubic }
        }
    }
}
