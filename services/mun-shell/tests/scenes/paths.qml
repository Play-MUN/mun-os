import QtQuick
import QtQuick.Window
import MUN.Shell

// A scene of tests/behaviour.py, loaded by the shell's own binary in place of
// its Main.qml (MUN_SHELL_QML_DIR): Home's network and object held still, and
// surfaces styled as the panel and a chosen entry, for comparing the drawing
// paths (src/renderinfo.h) frame for frame. The network does not drift
// (driftRate 0: its time stays at 0) and the object stands where Home puts
// it without its float; CONFIG's steps then change the object, send a wave,
// grab to PPM or quit at their times. It prints a PATHS line with the
// renderer once it is known.
Window {
    id: root
    visible: true
    width: Theme.canvasWidth
    height: Theme.canvasHeight
    color: "black"

    readonly property var config: CONFIG
    readonly property double started: Date.now()
    property int next: 0

    Binding { target: RenderInfo; property: "window"; value: root }
    Connections {
        target: RenderInfo
        function onDetected() { root.report("renderer") }
    }

    Item {
        id: canvas
        width: Theme.canvasWidth
        height: Theme.canvasHeight
        Backdrop {
            id: backdrop
            anchors.fill: parent
            orb: Theme.orb
            hour: root.config.hour !== undefined ? root.config.hour : 12
            driftRate: 0
            running: false
        }
        HeroIcon {
            id: hero
            width: 640
            height: 640
            x: Theme.orb.x - width / 2
            y: Theme.orb.y - height / 2
            icon: root.config.icon || "card"
            lit: root.config.lit !== undefined ? root.config.lit : 0.16
            running: false
        }
        // The panel's surface and shadow (DetailPanel.qml).
        Box {
            x: 1220
            y: 180
            width: 620
            height: 430
            radii: [26]
            gradient: ({ type: "linear", angle: 160, stops: [[0, Theme.rgba(62, 92, 130, 0.16)], [0.6, Theme.rgba(20, 24, 32, 0.35)], [1, Theme.rgba(7, 8, 10, 0.4)]] })
            insets: [{ y: 1, color: Theme.rgba(255, 255, 255, 0.08) }, { spread: 1, color: Theme.rgba(143, 176, 214, 0.10) }]
            Shadow {
                z: -1
                radii: [26]
                shadows: [{ y: 40, blur: 80, spread: -30, color: Theme.rgba(0, 0, 0, 0.8) }]
            }
        }
        // A chosen entry's bar, its glow and its knob (ArcNode.qml).
        Box {
            x: 1000
            y: 760
            width: 420
            height: 92
            radii: [46]
            gradient: ({ type: "linear", angle: 180, stops: [[0, "#E8E5DF"], [1, "#CCC9C3"]] })
            insets: [{ spread: 1.5, color: "#E39A63" }]
            Shadow {
                z: -1
                radii: [46]
                shadows: [{ blur: 16, color: Theme.rgba(227, 154, 99, 0.3) }, { y: 8, blur: 18, spread: -10, color: Theme.rgba(0, 0, 0, 0.7) }]
            }
        }
        Box {
            x: 1500
            y: 760
            width: 92
            height: 92
            radii: [46]
            gradient: ({ type: "radial", at: [0.4, 0.35], stops: [[0, Theme.rgba(62, 92, 130, 0.35)], [0.75, Theme.rgba(14, 17, 23, 0.9)]] })
            insets: [{ spread: 1, color: Theme.rgba(143, 176, 214, 0.22) }]
        }
    }

    function report(label) {
        console.log("PATHS " + JSON.stringify({
            label: label, t: Date.now() - started, api: RenderInfo.api, rhi: RenderInfo.rhi,
            accelerated: RenderInfo.accelerated, renderer: RenderInfo.renderer
        }))
    }

    function act(step) {
        if (step.icon !== undefined)
            hero.icon = step.icon
        if (step.ripple)
            backdrop.ripple()
        if (step.grab) {
            const name = step.grab
            canvas.grabToImage(function (result) {
                const path = root.config.out + "/" + name + ".ppm"
                console.log("GRAB " + JSON.stringify({ name: name, path: result.saveToFile(path) ? path : "" }))
            })
        }
        if (step.quit)
            Qt.quit()
    }

    function run() {
        while (next < config.steps.length && config.steps[next].at <= Date.now() - started)
            act(config.steps[next++])
        if (next < config.steps.length) {
            clock.interval = Math.max(1, config.steps[next].at - (Date.now() - started))
            clock.start()
        }
    }
    Timer { id: clock; onTriggered: root.run() }
    Component.onCompleted: run()
}
