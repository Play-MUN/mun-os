import QtQuick
import QtQuick.Window
import MUN.Shell

// A scene of tests/behaviour.py, loaded by the shell's own binary in place of
// its Main.qml (MUN_SHELL_QML_DIR). It gives the Shape controller the inputs
// of CONFIG's steps at their times (milliseconds from the start) and prints
// what Shape applies: one PROBE line per change, per change of its phase and
// per asked probe, one ADOPTED line per adoption. A step may also run the
// presence as QML does (begin, progress, arrived, leaveWith, left). Nothing is
// drawn.
Window {
    id: root
    visible: false
    width: 64
    height: 64

    readonly property var config: CONFIG
    readonly property double started: Date.now()
    property int next: 0

    function report(label) {
        console.log("PROBE " + JSON.stringify({
            label: label,
            source: Shape.source,
            insertion: Shape.insertion,
            dressed: Shape.dressed,
            window: Shape.window !== undefined && Shape.window !== null,
            cardShape: Shape.cardShape,
            morph: Shape.morph,
            glow: Shape.glow.valid ? String(Shape.glow) : "",
            ambient: Shape.ambient.valid ? String(Shape.ambient) : "",
            tint: Shape.tint.valid ? String(Shape.tint) : "",
            focus: String(Shape.focus),
            sounds: Object.keys(Shape.sounds).sort(),
            live: Shape.live,
            phase: Shape.phase,
            entry: Shape.entry,
            exit: Shape.exit,
            kind: Shape.kind,
            progress: Shape.progress,
            plated: Shape.plated,
            world: Object.keys(Shape.world).length > 0,
            transition: [Shape.transitionIn, Shape.transitionOut, Shape.seconds]
        }))
    }

    function act(step) {
        if (step.mode !== undefined)
            Shape.mode = step.mode
        if (step.arrival !== undefined)
            Shape.arrival = step.arrival
        // Several records in one step reach Shape in the same turn: only the
        // last may be applied.
        for (const card of (step.cards || []))
            Shape.card = card
        if (step.begin !== undefined)
            Shape.begin(step.begin)
        if (step.progress !== undefined)
            Shape.progress = step.progress
        if (step.arrived)
            Shape.arrived()
        if (step.leaveWith !== undefined)
            Shape.leaveWith(step.leaveWith)
        if (step.left)
            Shape.left()
        if (step.probe !== undefined)
            report(step.probe)
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

    Connections {
        target: Shape
        function onChanged() { root.report("changed") }
        function onPhaseChanged() { root.report("phase") }
        function onAdopted(live) { console.log("ADOPTED " + JSON.stringify({ insertion: Shape.insertion, live: live })) }
    }
}
