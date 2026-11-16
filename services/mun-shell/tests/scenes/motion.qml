import QtQuick
import QtQuick.Window
import MUN.Shell

// A scene of tests/behaviour.py, loaded by the shell's own binary in place of
// its Main.qml (MUN_SHELL_QML_DIR): the shell's MotionScene alone over black,
// given a package's motion and the inputs of CONFIG's steps at their times
// (the backend comes from MUN_SHELL_MOTION_BACKEND). It prints a MOTION line
// on each probe (drawn, failed, things listed, ticks, steps, backend, runs)
// and grabs to PPM. `eventsAt` gives events to apply at simulation steps
// (MotionScene.eventAt), the same on any machine whatever its frames.
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
        Rectangle { anchors.fill: parent; color: "black" }
        MotionScene {
            id: scene
            anchors.fill: parent
            disc: Qt.point(960, 540)
        }
    }

    function report(label) {
        console.log("MOTION " + JSON.stringify({
            label: label, t: Date.now() - started, drawn: scene.drawn, failed: scene.failed, things: scene.things,
            ticks: scene.ticks, steps: scene.steps, backend: scene.backend, runs: scene.runs
        }))
    }

    function act(step) {
        // the package's parts a step changes, set together (the others kept)
        if (step.insertion !== undefined || step.root !== undefined || step.motion !== undefined) {
            const parts = Object.assign({}, scene.source)
            for (const key of ["insertion", "root", "motion"])
                if (step[key] !== undefined)
                    parts[key] = step[key]
            scene.source = parts
        }
        for (const key of ["running", "still"])
            if (step[key] !== undefined)
                scene[key] = step[key]
        if (step.event)
            scene.event(step.event)
        for (const name of step.events || [])
            scene.event(name)
        for (const [name, at] of step.eventsAt || [])
            scene.eventAt(name, at)
        if (step.actor)
            console.log("ACTOR " + JSON.stringify(Object.assign({ label: step.label || step.actor, steps: scene.steps },
                                                                 scene.actorAt(step.actor))))
        if (step.probe)
            report(step.probe)
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
