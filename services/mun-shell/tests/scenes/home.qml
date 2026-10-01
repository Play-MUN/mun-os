import QtQuick
import MUN.Shell

// A scene of tests/behaviour.py: the shell's own Main.qml, compiled in, made
// here as the binary makes it after a game (no start-up), against the
// stand-ins for the card service and the launcher the test runs. What a
// player would do comes from CONFIG's steps (a call of one of Main's own
// functions, at a time). It prints a PHASE line whenever Shape's phase
// changes, and grabs the canvas to PPM files at the moments CONFIG.grabs
// names (a phase and the progress it must reach, after a time if `after` is
// given; or `first: "shaped"`, the first frame an entry is dressed), with a
// GRAB line saying where each surface's text lies, how far the transition
// has reached it and each entry's opacity, its own and on screen (`effective`:
// with every item above it). With CONFIG.trace it prints a TRACE line on each
// frame anything of the entries' changed: whether each is dressed, dimmed,
// its opacities, and whether a dialog's layer is shown.
Item {
    id: scene
    readonly property var config: CONFIG
    readonly property double started: Date.now()
    property var main: null
    property var canvas: null
    property var world: null
    property var modalLayer: null
    property int next: 0
    property var pendingGrabs: (config.grabs || []).slice()

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
    function findAll(item, test, into) {
        if (!item)
            return into
        if (test(item))
            into.push(item)
        const children = item.children || []
        for (let i = 0; i < children.length; ++i)
            findAll(children[i], test, into)
        return into
    }

    // As surfaces.qml: a flat rectangle of a projected layer, in the canvas.
    function projected(layer, x0, y0, x1, y1, inner) {
        const m = layer.margin
        const corners = [[x0, y0], [x1, y0], [x0, y1], [x1, y1]].map(p => {
            const v = layer.projection.times(Qt.vector3d(p[0] + m, p[1] + m, 0))
            return layer.mapToItem(canvas, v.x - m, v.y - m)
        })
        const [a, b, c, d] = corners
        if (inner)
            return [Math.ceil(Math.max(a.x, c.x)), Math.ceil(Math.max(a.y, b.y)),
                    Math.floor(Math.min(b.x, d.x)), Math.floor(Math.min(c.y, d.y))]
        return [Math.floor(Math.min(a.x, c.x)), Math.floor(Math.min(a.y, b.y)),
                Math.ceil(Math.max(b.x, d.x)), Math.ceil(Math.max(c.y, d.y))]
    }

    // An item's opacity on screen: its own times every item's above it.
    function effective(item) {
        let o = 1
        for (let it = item; it; it = it.parent) {
            o *= it.opacity
            if (it === canvas)
                break
        }
        return o
    }
    function mainEntries() {
        return findAll(canvas, it => it.knobSize !== undefined && it.reached !== undefined && it.dressed, [])
    }

    function regions() {
        const entries = []
        for (const node of findAll(canvas, it => it.knobSize !== undefined && it.reached !== undefined, [])) {
            if (!node.visible || node.opacity <= 0.01 || !node.label || node.parent.opacity <= 0.01)
                continue
            const left = node.knobSize + node.wireWidth + (node.on ? 4 : 0)
            const width = node.width - node.knobSize - node.wireWidth - 4
            entries.push({ label: node.label, chosen: node.on, reached: node.reached, shaped: node.shaped,
                           opacity: node.opacity, effective: effective(node),
                           box: projected(node, left + 14, 9, left + width - 34, node.barHeight - 9, true) })
        }
        const panel = find(canvas, it => it.plating !== undefined)
        return { entries: entries,
                 panel: panel ? { reached: panel.reached, shaped: panel.shaped, dressed: panel.dressed,
                                  text: projected(panel, 52, 40, panel.width - 52, 200, true),
                                  outline: projected(panel, 0, 0, panel.width, panel.height, false) } : null }
    }

    function grab(name) {
        const where = regions()
        const at = { name: name, t: now(), phase: Shape.phase, progress: Shape.progress, kind: Shape.kind,
                     dim: world ? world.dim : 0, regions: where }
        canvas.grabToImage(function (result) {
            const path = scene.config.out + "/" + name + ".ppm"
            at.path = result.saveToFile(path) ? path : ""
            console.log("GRAB " + JSON.stringify(at))
        })
    }

    function report(label) {
        console.log("PHASE " + JSON.stringify({
            label: label, t: now(), phase: Shape.phase, entry: Shape.entry, exit: Shape.exit, kind: Shape.kind,
            progress: Shape.progress, source: Shape.source, insertion: Shape.insertion,
            world: Object.keys(Shape.world).length > 0,
            worldDrawn: world ? world.drawn : false, worldFailed: world ? world.failed : false,
            frames: world ? world.frames : 0, still: world ? world.still : false, dim: world ? world.dim : 0,
            modal: main ? !!main.modal : false, level: main ? main.level : -1
        }))
    }

    function act(step) {
        if (step.call) {
            const args = step.args || []
            main[step.call].apply(main, args)
        }
        if (step.set)
            for (const key in step.set)
                main[key] = step.set[key]
        if (step.settings)
            for (const key in step.settings)
                ShellSettings[key] = step.settings[key]
        if (step.probe)
            report(step.probe)
        if (step.grab)
            grab(step.grab)
        if (step.quit)
            Qt.quit()
    }

    function run() {
        while (next < config.steps.length && config.steps[next].at <= now())
            act(config.steps[next++])
        if (next < config.steps.length) {
            clock.interval = Math.max(1, config.steps[next].at - now())
            clock.start()
        }
    }
    Timer { id: clock; onTriggered: scene.run() }

    // Grabs at points of a transition: taken the first frame the phase is
    // the one named and the progress has passed the point.
    Timer {
        interval: 16
        repeat: true
        running: scene.pendingGrabs.length > 0 && scene.canvas !== null
        onTriggered: {
            const rest = []
            for (const g of scene.pendingGrabs) {
                const passed = g.after !== undefined && scene.now() < g.after ? false
                             : g.first === "shaped" ? scene.mainEntries().some(n => n.shaped)
                             : g.phase === "present" ? Shape.phase === "present"
                             : Shape.phase === g.phase && (g.phase === "leaving" ? Shape.progress <= g.at : Shape.progress >= g.at)
                if (passed)
                    scene.grab(g.name)
                else
                    rest.push(g)
            }
            scene.pendingGrabs = rest
        }
    }

    // The entries frame by frame (CONFIG.trace).
    property string lastTrace: ""
    Timer {
        interval: 16
        repeat: true
        running: !!scene.config.trace && scene.canvas !== null
        onTriggered: {
            const entries = scene.mainEntries().filter(n => n.label).map(n => ({
                label: n.label, shaped: n.shaped, dimmed: n.dimmed,
                opacity: Math.round(n.opacity * 1000) / 1000, effective: Math.round(scene.effective(n) * 1000) / 1000 }))
            const state = { phase: Shape.phase, modalShown: !!scene.modalLayer && scene.modalLayer.visible, entries: entries }
            const key = JSON.stringify(state)
            if (key === scene.lastTrace)
                return
            scene.lastTrace = key
            console.log("TRACE " + JSON.stringify(Object.assign({ t: scene.now(), progress: Shape.progress }, state)))
        }
    }

    Connections {
        target: SystemSounds
        function onGameSoundQueued(name) { console.log("SOUND " + JSON.stringify({ t: scene.now(), name: name, phase: Shape.phase })) }
    }
    Connections {
        target: Shape
        function onPhaseChanged() { scene.report("phase") }
        function onAdopted(live) { console.log("ADOPTED " + JSON.stringify({ t: scene.now(), insertion: Shape.insertion, live: live })) }
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
        world = find(canvas, it => it.covering !== undefined && it.transition !== undefined)
        modalLayer = find(canvas, it => it.shownContent !== undefined)
        report("start")
        run()
    }
}
