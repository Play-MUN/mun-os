import QtQuick
import QtQuick.Window
import MUN.Shell

// A scene of tests/behaviour.py, loaded by the shell's own binary in place of
// its Main.qml (MUN_SHELL_QML_DIR). Shape is fed from CardClient as Main.qml
// feeds it, and CardClient from the stand-in card service the test runs on
// MUN_CARDD_SOCKET. It prints one ADOPTED line per adoption and quits after
// CONFIG.end milliseconds. Nothing is drawn.
Window {
    id: root
    visible: false
    width: 64
    height: 64

    readonly property var config: CONFIG

    Binding { target: Shape; property: "card"; value: CardClient.card }
    Binding { target: Shape; property: "arrival"; value: CardClient.arrival }

    Connections {
        target: Shape
        function onAdopted(live) {
            console.log("ADOPTED " + JSON.stringify({ insertion: Shape.insertion, live: live, arrival: CardClient.arrival }))
        }
    }
    Timer { interval: root.config.end; running: true; onTriggered: Qt.quit() }
}
