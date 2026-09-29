import QtQuick
import MUN.Shell

// The line at the top right: the slot, the network, tonight's moon and the
// time. The slot's light is the console's LED colour while a Game Card is
// ready; the network's is patina while an interface is connected.
Row {
    id: root
    property string slotText
    property bool cardReady: false
    property string networkText
    property bool online: false
    property string clockFormat: "24h"
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
        Shadow {
            z: -1
            radii: [5]
            shadows: light.glow
            opacity: light.lit ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 400; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }
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
        Status { text: root.slotText }
    }
    Row {
        anchors.verticalCenter: parent.verticalCenter
        spacing: 10
        Light {
            lit: root.online
            litColour: Theme.patina
            glow: [{ blur: 10, spread: 2, color: Theme.rgba(127, 179, 163, 0.6) }]
        }
        Status { text: root.networkText }
    }
    Status { text: I18n.moonName(moon.phase) }
    MoonPhase {
        id: moon
        anchors.verticalCenter: parent.verticalCenter
        width: 24
        height: 24
        phase: phaseAt(root.now)
    }
    MarkText {
        id: clock
        anchors.verticalCenter: parent.verticalCenter
        text: I18n.clock(root.now, root.clockFormat)
        size: 21
        tracking: 0.14
        color: Theme.moon
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
