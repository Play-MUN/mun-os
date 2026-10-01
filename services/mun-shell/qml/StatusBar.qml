import QtQuick
import MUN.Shell

// The line at the top right: the slot, the network, tonight's moon and the
// time. The slot's light is the console's LED colour while a Game Card is
// ready; the network's is patina while an interface is connected. On a
// Game Card's band (Shape, docs/shape.md) its words and clock take the
// game's text colour; the lights keep their meaning and colours, each on a
// socket of MUN's own (`socketed`: a dark disc with a fine rim), so they show
// as MUN draws them on any band.
Row {
    id: root
    property string slotText
    property bool cardReady: false
    property string networkText
    property bool online: false
    property string clockFormat: "24h"
    property color textColour: Theme.ash
    property color clockColour: Theme.moon
    property bool socketed: false
    // The local time, to the minute.
    property date now: new Date()

    spacing: 30

    component Light: Rectangle {
        id: light
        property bool lit: false
        property color litColour
        property var glow: []
        width: 10
        height: 10
        radius: 5
        anchors.verticalCenter: parent.verticalCenter
        color: lit ? litColour : Theme.unlit
        Behavior on color { ColorAnimation { duration: 400; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }
        Socket { size: 22 }
        Shadow {
            z: -1
            radii: [5]
            shadows: light.glow
            opacity: light.lit ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 400; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }
        }
    }
    // MUN's own ground under a light, over whatever band is beneath: whole
    // from the first frame the band is the game's, so a light is never shown
    // on it without its ground; it eases away once the band is MUN's again
    // (`away` is held at 1 meanwhile).
    component Socket: Rectangle {
        id: socket
        property int size
        property real away: 0
        z: -2
        anchors.centerIn: parent
        width: size
        height: size
        radius: size / 2
        color: Theme.dialog
        border.width: 1
        border.color: Theme.rgba(218, 215, 209, 0.22)
        opacity: root.socketed ? 1 : away
        visible: opacity > 0
        Component.onCompleted: away = root.socketed ? 1 : 0
        NumberAnimation {
            id: easingAway
            target: socket
            property: "away"
            to: 0
            duration: 400
            easing.type: Easing.BezierSpline
            easing.bezierCurve: Theme.ease
        }
        Connections {
            target: root
            function onSocketedChanged() {
                easingAway.stop()
                if (root.socketed)
                    socket.away = 1
                else
                    easingAway.start()
            }
        }
    }
    component Status: UiText {
        anchors.verticalCenter: parent.verticalCenter
        size: 23
        color: Theme.ash
    }

    Row {
        anchors.verticalCenter: parent.verticalCenter
        spacing: 10
        Light {
            lit: root.cardReady
            litColour: Theme.led
            glow: [{ blur: 12, spread: 2, color: Theme.rgba(221, 233, 255, 0.8) }]
        }
        Status { text: root.slotText; color: root.textColour }
    }
    Row {
        anchors.verticalCenter: parent.verticalCenter
        spacing: 10
        Light {
            lit: root.online
            litColour: Theme.patina
            glow: [{ blur: 10, spread: 2, color: Theme.rgba(127, 179, 163, 0.6) }]
        }
        Status { text: root.networkText; color: root.textColour }
    }
    Status { text: I18n.moonName(moon.phase); color: root.textColour }
    MoonPhase {
        id: moon
        anchors.verticalCenter: parent.verticalCenter
        width: 24
        height: 24
        phase: phaseAt(root.now)
        Socket { size: 34 }
    }
    MarkText {
        id: clock
        anchors.verticalCenter: parent.verticalCenter
        text: I18n.clock(root.now, root.clockFormat)
        size: 21
        tracking: 0.14
        color: root.clockColour
    }

    Timer {
        // Fires just after each minute begins.
        interval: 1000 * (60 - new Date().getSeconds())
        running: true
        repeat: true
        onTriggered: {
            root.now = new Date()
            interval = 1000 * (60 - root.now.getSeconds())
        }
    }
}
