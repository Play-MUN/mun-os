import QtQuick
import MUN.Shell

// A scene of tests/behaviour.py: the shell's own Main.qml, compiled in, made
// here as the binary makes it after a game (no start-up), against the
// stand-ins for the card service and the launcher the test runs. What a
// player would do comes from CONFIG's steps, in order: a call of one of
// Main's own functions, a property set, a probe or a grab, each when it is
// due: at a time (`at`), some time after the step before (`wait`), and once
// the state it waits for holds (`when`: fields of the PHASE line, such as
// the phase, the menu's level or the card's state), at most `within` ms
// (15 s by default; then a TIMEOUT line, and the step acts anyway). The
// test's stand-in services follow the probes' and phases' lines, so a
// player's step and a card's event come in the order the case says, however
// slowly the shell goes. It prints a PHASE line whenever Shape's phase
// changes, and grabs the canvas to PPM files at the moments CONFIG.grabs
// names (a phase and the progress it must reach, after a time or a step's
// `mark` if `after` is given; or `first: "shaped"`, the first frame an entry
// is dressed), with a
// GRAB line saying where each surface's text lies, how far the transition
// has reached it and each entry's opacity, its own and on screen (`effective`:
// with every item above it). With CONFIG.trace it prints a TRACE line on each
// frame anything of the entries' changed: whether each is dressed, dimmed,
// its opacities, and whether a dialog's layer is shown.
//
// The menu entries are found in either design: the previous design's arcs
// (ArcNode, `knobSize`) or the orbital design's (OrbitalNode, `nodeX`); the
// card object likewise (HeroIcon or OrbitalCard, `cardShape` and
// `cardReveal`). The start line says which design came up and how many of
// each it found, for the test to require them.
Item {
    id: scene
    readonly property var config: CONFIG
    readonly property double started: Date.now()
    property var main: null
    property var canvas: null
    property var world: null
    property var modalLayer: null
    property int next: 0
    // When the step before acted, and since when the step in turn has been
    // due and waiting for its state.
    property double lastDone: 0
    property double waitingSince: -1
    property var marks: ({})
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
    function isEntry(it) { return (it.knobSize !== undefined || it.nodeX !== undefined) && it.reached !== undefined }
    // The item that draws the card object (HeroIcon, OrbitalCard): the
    // innermost with its properties, which the items around it may pass on.
    function isCard(it) {
        const has = c => c.cardShape !== undefined && c.cardReveal !== undefined
        return has(it) && findAll(it, c => c !== it && has(c), []).length === 0
    }
    function mainEntries() {
        return findAll(canvas, it => isEntry(it) && it.dressed, [])
    }

    // Where an entry's text lies on its plate, in the canvas: an arc's bar
    // through its projection, an orbital pill flat.
    function textBox(node) {
        if (node.nodeX !== undefined) {
            const b = node.box
            const left = node.on || node.icon === "" ? 30 : (node.compact ? 72 : 86)
            const a = node.mapToItem(canvas, b.x + left - 12, b.y + 9)
            const c = node.mapToItem(canvas, b.x + b.width - 30, b.y + b.height - 9)
            return [Math.ceil(a.x), Math.ceil(a.y), Math.floor(c.x), Math.floor(c.y)]
        }
        const left = node.knobSize + node.wireWidth + (node.on ? 4 : 0)
        const width = node.width - node.knobSize - node.wireWidth - 4
        return projected(node, left + 14, 9, left + width - 34, node.barHeight - 9, true)
    }

    function regions() {
        const entries = []
        for (const node of findAll(canvas, isEntry, [])) {
            if (!node.visible || node.opacity <= 0.01 || !node.label || node.parent.opacity <= 0.01)
                continue
            entries.push({ label: node.label, chosen: node.on, reached: node.reached, shaped: node.shaped,
                           opacity: node.opacity, effective: effective(node), box: textBox(node) })
        }
        // The card object: where it is, whether it shows a window, how far.
        const cards = findAll(canvas, isCard, []).map(it => {
            const a = it.mapToItem(canvas, 0, 0), c = it.mapToItem(canvas, it.width, it.height)
            return { box: [Math.floor(a.x), Math.floor(a.y), Math.ceil(c.x), Math.ceil(c.y)],
                     window: it.cardWindow !== undefined && it.cardWindow !== null, reveal: it.cardReveal,
                     shape: it.cardShape, shown: it.visible && effective(it) > 0.5 }
        })
        const panel = find(canvas, it => it.plating !== undefined)
        return { entries: entries, cards: cards,
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
        console.log("PHASE " + JSON.stringify(snapshot(label)))
    }

    function snapshot(label) {
        return {
            label: label, t: now(), phase: Shape.phase, entry: Shape.entry, exit: Shape.exit, kind: Shape.kind,
            progress: Shape.progress, source: Shape.source, insertion: Shape.insertion,
            world: Object.keys(Shape.world).length > 0,
            worldDrawn: world ? world.drawn : false, worldFailed: world ? world.failed : false,
            frames: world ? world.frames : 0, still: world ? world.still : false, dim: world ? world.dim : 0,
            modal: main ? !!main.modal : false, level: main ? main.level : -1,
            dimmed: main ? main.dimmed : false, mainIndex: main ? main.mainIndex : -1,
            sections: main ? main.settingsEntries.map(e => e.key) : [],
            design: main ? (main.orbital ? "orbital" : "previous") : "",
            entriesFound: canvas ? findAll(canvas, isEntry, []).length : 0,
            cardsFound: canvas ? findAll(canvas, isCard, []).length : 0,
            card: main ? main.cardMode : "",
            // Texts on screen cut short (an elided label).
            cut: canvas ? findAll(canvas, it => it.truncated === true && it.visible && effective(it) > 0.5, [])
                              .map(it => it.text) : []
        }
    }

    // A step's `when`, polled every 10 ms: only the fields that cost nothing
    // to read (no walk through the scene's items).
    function holds(when) {
        const state = {
            phase: Shape.phase, entry: Shape.entry, exit: Shape.exit, kind: Shape.kind, source: Shape.source,
            insertion: Shape.insertion, world: Object.keys(Shape.world).length > 0,
            modal: main ? !!main.modal : false, level: main ? main.level : -1, dimmed: main ? main.dimmed : false,
            mainIndex: main ? main.mainIndex : -1, card: main ? main.cardMode : ""
        }
        for (const key in when) {
            if (state[key] !== when[key])
                return false
        }
        return true
    }

    function act(step) {
        if (step.mark)
            marks[step.mark] = now()
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
        while (next < config.steps.length) {
            const step = config.steps[next]
            const t = now()
            if ((step.at !== undefined && t < step.at) || (step.wait !== undefined && t < lastDone + step.wait))
                return
            if (step.when !== undefined && !holds(step.when)) {
                if (waitingSince < 0)
                    waitingSince = t
                const within = step.within !== undefined ? step.within : 15000
                if (t - waitingSince < within)
                    return
                console.log("TIMEOUT " + JSON.stringify({ t: t, step: next, when: step.when, within: within,
                                                          state: snapshot("timeout") }))
            }
            waitingSince = -1
            next++
            lastDone = t
            act(step)
        }
    }
    Timer {
        id: clock
        interval: 10
        repeat: true
        running: scene.main !== null && scene.next < scene.config.steps.length
        onTriggered: scene.run()
    }

    // Grabs at points of a transition: taken the first frame the phase is
    // the one named and the progress has passed the point.
    Timer {
        interval: 16
        repeat: true
        running: scene.pendingGrabs.length > 0 && scene.canvas !== null
        onTriggered: {
            const rest = []
            for (const g of scene.pendingGrabs) {
                const waiting = g.after === undefined ? false
                              : typeof g.after === "string" ? scene.marks[g.after] === undefined : scene.now() < g.after
                const passed = waiting || (g.insertion !== undefined && Shape.insertion !== g.insertion) ? false
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
        function onQueued(name) { console.log("SYSSOUND " + JSON.stringify({ t: scene.now(), name: name })) }
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
