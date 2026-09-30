import QtQuick
import QtQuick.Window
import MUN.Shell

// A scene of tests/behaviour.py, loaded by the shell's own binary in place of
// its Main.qml (MUN_SHELL_QML_DIR). The main arc and the game's panel, the
// shell's own components placed as Main.qml places them, dressed by the card
// of CONFIG over a flat world of the shot's colour. Once Shape has applied
// the card's identity, each of CONFIG.shots is set (Home: the arc has the
// focus; options: the panel's first option has it and the arc is dimmed, as
// Main.qml dims it), left to settle, and grabbed to a PPM file. A SHOT line
// gives where each surface's text lies in the grab: inside each bar of the
// arc and inside the panel's text, through the same projections the views
// draw with, and the panel's outline.
Window {
    id: root
    visible: true
    width: Theme.canvasWidth
    height: Theme.canvasHeight
    color: "black"

    readonly property var config: CONFIG
    property int shot: -1
    property color world: "black"
    property bool options: false

    Item {
        id: canvas
        width: Theme.canvasWidth
        height: Theme.canvasHeight

        Rectangle {
            anchors.fill: parent
            color: root.world
        }
        ArcMenu {
            id: arc
            anchors.fill: parent
            entries: root.config.entries
            current: 0
            radius: 340
            spread: 24
            dressed: true
            dimmed: root.options
        }
        DetailPanel {
            id: panel
            content: root.config.panel
            dressed: true
            selected: root.options ? 0 : -1
        }
    }

    // A flat rectangle of a projected layer's content, in the canvas: the
    // largest upright rectangle inside its projected outline (`inner`), or
    // the smallest one around it.
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

    function regions() {
        const entries = []
        for (const node of arc.children) {
            if (node.knobSize === undefined)
                continue
            // The bar, clear of its rounded ends and edges.
            const left = node.knobSize + node.wireWidth + (node.on ? 4 : 0)
            const width = node.width - node.knobSize - node.wireWidth - 4
            entries.push({ label: node.label, chosen: node.on,
                           box: projected(node, left + 14, 9, left + width - 34, node.barHeight - 9, true) })
        }
        return { entries: entries,
                 // The kicker, the title and the text, above the options.
                 panelText: projected(panel, 52, 40, panel.width - 52, 200, true),
                 panel: projected(panel, 0, 0, panel.width, panel.height, false) }
    }

    function take() {
        const s = root.config.shots[root.shot]
        canvas.grabToImage(function (result) {
            const path = root.config.out + "/" + s.name + ".ppm"
            if (!result.saveToFile(path))
                console.log("SHOT-FAILED " + path)
            console.log("SHOT " + JSON.stringify({ name: s.name, path: path, regions: root.regions() }))
            root.advance()
        })
    }

    function advance() {
        root.shot += 1
        if (root.shot >= root.config.shots.length) {
            Qt.quit()
            return
        }
        const s = root.config.shots[root.shot]
        root.world = s.world
        root.options = s.state === "options"
        settle.restart()
    }

    Timer { id: settle; interval: root.config.settle; onTriggered: root.take() }

    Component.onCompleted: Shape.card = root.config.card
    Connections {
        target: Shape
        function onChanged() {
            if (root.shot < 0 && Shape.source === "shape")
                root.advance()
        }
    }
    Timer { interval: root.config.timeout; running: true; onTriggered: { console.log("SCENE-TIMEOUT"); Qt.quit() } }
}
