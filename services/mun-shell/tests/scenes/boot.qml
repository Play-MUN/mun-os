import QtQuick
import MUN.Shell

// A scene of tests/behaviour.py: the shell's own Main.qml, compiled in, made
// here as the binary makes it on the first start of a boot (the start-up
// plays), against the stand-ins for the card service and the launcher the
// test runs. Every 100 ms it prints a BOOT line: the start-up's clock (t),
// whether it plays, waits for the system (the LED breathing), may be skipped
// and shows its hint (-1: the layer's drawing is gone), whether Home is
// powered and whether the memory has settled (src/memory.h). CONFIG's steps
// press a key at a time ({"at": ms, "key": "A"}: the key goes through Main's
// own handling) or end the scene.
Item {
    id: scene
    readonly property var config: CONFIG
    readonly property double started: Date.now()
    property var main: null
    property var canvas: null
    property var boot: null
    property int next: 0

    function now() { return Date.now() - started }

    function find(item, test) {
        if (!item)
            return null
        if (test(item))
            return item
        const children = item.children || []
        for (let i = 0; i < children.length; ++i) {
            const found = find(children[i], test)
            if (found)
                return found
        }
        return null
    }

    function state() {
        // Looked up each time: the layer lets its drawing go once it has left.
        const hint = find(boot, it => it.objectName === "skipHint")
        return { t: Math.round(boot.t * 100) / 100, playing: boot.playing, waiting: boot.waiting, canSkip: boot.canSkip,
                 hint: hint ? Math.round(hint.opacity * 100) / 100 : -1, shown: boot.shown,
                 ready: boot.ready, powered: main.powered, settled: Memory.settled }
    }
    function report() {
        console.log("BOOT " + JSON.stringify(Object.assign({ at: now() }, state())))
    }

    // A key as a player presses it, through Main's own handling of keys.
    function press(name) {
        const key = { "A": Qt.Key_A, "B": Qt.Key_B, "Enter": Qt.Key_Return, "Down": Qt.Key_Down }[name]
        console.log("KEY " + JSON.stringify({ at: now(), key: name, canSkip: boot.canSkip }))
        main.key(key, false)
    }

    function run() {
        while (next < config.steps.length && config.steps[next].at <= now()) {
            const step = config.steps[next++]
            if (step.key)
                press(step.key)
            if (step.quit) {
                report()
                Qt.quit()
            }
        }
        if (next < config.steps.length) {
            clock.interval = Math.max(1, config.steps[next].at - now())
            clock.start()
        }
    }
    Timer { id: clock; onTriggered: scene.run() }
    Timer {
        interval: 100
        repeat: true
        running: scene.boot !== null
        onTriggered: scene.report()
    }

    Component.onCompleted: {
        const component = Qt.createComponent("qrc:/qt/qml/MUN/Shell/Main.qml")
        if (component.status !== Component.Ready) {
            console.log("SCENE-ERROR " + component.errorString())
            Qt.quit()
            return
        }
        main = component.createObject(null)
        canvas = find(main.contentItem, it => it.width === Theme.canvasWidth && it.height === Theme.canvasHeight && it.focus === true)
        boot = find(canvas, it => it.canSkip !== undefined && it.waiting !== undefined)
        report()
        run()
    }
}
