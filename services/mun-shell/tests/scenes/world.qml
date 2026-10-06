import QtQuick
import QtQuick.Window
import MUN.Shell

// A scene of tests/behaviour.py, loaded by the shell's own binary in place of
// its Main.qml (MUN_SHELL_QML_DIR): the shell's ShapeWorld alone over a flat
// colour, given the world and the inputs of CONFIG's steps at their times.
// It prints a WORLD line on every change of its state and on each probe
// (ready, drawn, failed, detail, frames shown, covering), and grabs to PPM.
Window {
    id: root
    visible: true
    width: Theme.canvasWidth
    height: Theme.canvasHeight
    color: "black"

    readonly property var config: CONFIG
    readonly property double started: Date.now()
    property int next: 0

    Item {
        id: canvas
        width: Theme.canvasWidth
        height: Theme.canvasHeight
        Rectangle { anchors.fill: parent; color: root.config.under || "#000000" }
        ShapeWorld {
            id: world
            anchors.fill: parent
            orb: Theme.orb
            onPreparedChanged: root.report("state")
        }
    }

    function report(label) {
        console.log("WORLD " + JSON.stringify({
            label: label, t: Date.now() - started, ready: world.ready, drawn: world.drawn, failed: world.failed,
            detail: world.detail, frames: world.frames, covering: world.covering, estimate: world.estimate,
            budget: world.budget, progress: world.progress
        }))
    }

    function act(step) {
        for (const key of ["root", "world", "transition", "progress", "still", "running", "dim", "parallax"])
            if (step[key] !== undefined)
                world[key] = step[key]
        if (step.stepDown)
            world.stepDown(step.stepDown)
        if (step.probe)
            report(step.probe)
        if (step.grab) {
            const name = step.grab
            canvas.grabToImage(function (result) {
                const path = root.config.out + "/" + name + ".ppm"
                console.log("GRAB " + JSON.stringify({ name: name, path: result.saveToFile(path) ? path : "",
                                                       frames: world.frames, progress: world.progress }))
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
