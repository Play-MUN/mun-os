import QtQuick
import MUN.Shell

// The orbital design's icons on a 24-unit grid at `size` pixels: "disk" (a
// MUN Disk: its hole, its key notch, its label's ring), "universe",
// "settings", "power", and Settings' sections, "display", "sound",
// "appearance", "language", "network", "system". One stroke for all, round
// at its ends and joints; `style` "duotone" also fills their key areas at a
// fifth of the colour. An unknown name draws nothing.
//
// Still at rest, each has a gesture of its own that play() runs once: the
// disk turns a quarter, the satellite goes once round the planet and comes
// to rest, the gear turns a quarter, the power sign is pressed, a line
// sweeps the screen, the sound's waves and the network's arcs light in
// turn, the contrast turns half round, the globe spins half round, the
// console's light blinks twice. A gesture is a short animation (1 s), drawn
// on every frame of the display while it runs; it ends at rest, the disk's
// label a quarter on, the contrast half round, the gear a quarter (its teeth
// repeat every eighth).
// Every part is painted once, into a layer of its
// own, and moves by a transform or its opacity: nothing is painted again
// while it moves. Reduce motion: no gestures.
Item {
    id: root
    property string name
    property int size: 34
    property color colour: Theme.moon
    property string style: "line"
    // The stroke in grid units: a large icon takes a finer one (OrbitalFocus).
    property real stroke: 1.7

    // Gestures played so far, and how far the current one has gone: rest at
    // a whole number. `phase` runs 0 to 1 through a gesture and is 0 at rest.
    property real turns: 0
    property int played: 0
    readonly property real phase: Math.max(0, Math.min(1, turns - played))
    readonly property real thin: stroke * 0.65

    function play() {
        if (ShellSettings.reduceMotion || name === "")
            return
        gesture.stop()
        played = Math.round(turns)
        turns = played
        gesture.to = played + 1
        gesture.start()
    }
    NumberAnimation {
        id: gesture
        target: root
        property: "turns"
        duration: 1000
        // A slight overshoot, so that it settles into place.
        easing.type: Easing.OutBack
        easing.overshoot: 1.1
        onFinished: root.played = Math.round(root.turns)
    }

    width: size
    height: size
    readonly property real unit: size / 24
    // A bump over part of the gesture: 0 outside [from, from + span], up to 1.
    function bump(from, span) {
        const x = (phase - from) / span
        return x <= 0 || x >= 1 ? 0 : Math.sin(Math.PI * x)
    }

    // The parts of each icon: `base`, still, and `layers`, each drawn once
    // and moved by `rotation` (degrees about the grid's centre), `opacity`,
    // `dx`/`dy` (grid units) and `xScale` (about the centre), all functions
    // of the gesture.
    readonly property var parts: {
        const circle = (ctx, x, y, r) => { ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI) }
        switch (name) {
        case "disk":
            return {
                base: (ctx, fill) => {
                    ctx.beginPath(); ctx.arc(12, 12, 9.5, 0, 2 * Math.PI); ctx.arc(12, 12, 2.6, 0, 2 * Math.PI, true); fill()
                    circle(ctx, 12, 12, 9.5); ctx.stroke()
                    circle(ctx, 12, 12, 2.6); ctx.stroke()
                    ctx.beginPath(); ctx.moveTo(12, 9.4); ctx.lineTo(12, 8.3); ctx.stroke()
                },
                layers: [{ draw: (ctx) => { ctx.lineWidth = thin; ctx.beginPath(); ctx.arc(12, 12, 6.3, Math.PI * 1.15, Math.PI * 1.85); ctx.stroke() },
                           rotation: () => 90 * turns }]
            }
        case "universe": {
            // The satellite on the orbit, a tilted ellipse: once round per
            // gesture, fainter while behind the planet.
            const at = () => {
                const a = -0.6 + 2 * Math.PI * turns, ox = 10.4 * Math.cos(a), oy = 3.744 * Math.sin(a)
                return { x: ox * Math.cos(-0.36) - oy * Math.sin(-0.36), y: ox * Math.sin(-0.36) + oy * Math.cos(-0.36), behind: Math.sin(a) < 0 }
            }
            return {
                base: (ctx, fill) => {
                    const orbit = (from, to) => { ctx.save(); ctx.translate(12, 12); ctx.rotate(-0.36); ctx.scale(1, 0.36); ctx.beginPath(); ctx.arc(0, 0, 10.4, from, to); ctx.restore(); ctx.stroke() }
                    orbit(Math.PI * 1.08, Math.PI * 1.92)
                    // The planet hides the orbit's far side behind it.
                    ctx.save(); ctx.globalCompositeOperation = "destination-out"; ctx.fillStyle = "black"
                    circle(ctx, 12, 12, 4.6); ctx.fill(); ctx.restore()
                    circle(ctx, 12, 12, 4.6); fill()
                    circle(ctx, 12, 12, 4.6); ctx.stroke()
                    orbit(Math.PI * 0.02, Math.PI * 0.98)
                },
                layers: [{ draw: (ctx) => { ctx.fillStyle = ctx.strokeStyle; circle(ctx, 12, 12, 1.35); ctx.fill() },
                           dx: () => at().x, dy: () => at().y, opacity: () => at().behind ? 0.35 : 1 }]
            }
        }
        case "settings":
            return {
                base: (ctx) => {},
                layers: [{ draw: (ctx, fill) => {
                               ctx.beginPath()
                               for (let i = 0; i < 8; ++i) {
                                   const a = i * Math.PI / 4
                                   const points = [[7.0, a - 0.30], [9.6, a - 0.17], [9.6, a + 0.17], [7.0, a + 0.30]]
                                   for (let j = 0; j < 4; ++j) {
                                       const px = 12 + points[j][0] * Math.cos(points[j][1]), py = 12 + points[j][0] * Math.sin(points[j][1])
                                       if (i === 0 && j === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py)
                                   }
                               }
                               ctx.closePath(); fill(); ctx.stroke()
                               circle(ctx, 12, 12, 3.1); ctx.stroke()
                           },
                           rotation: () => 90 * turns }]
            }
        case "power":
            // Pressed: the stem goes down a little and the ring dims, then
            // both come back.
            return {
                base: (ctx) => {},
                layers: [{ draw: (ctx) => { ctx.beginPath(); ctx.moveTo(12, 3.2); ctx.lineTo(12, 11.4); ctx.stroke() },
                           dy: () => 1.4 * bump(0, 0.6) },
                         { draw: (ctx) => { ctx.beginPath(); ctx.arc(12, 13, 8, -Math.PI / 2 + 0.68, -Math.PI / 2 - 0.68 + 2 * Math.PI); ctx.stroke() },
                           opacity: () => 1 - 0.5 * bump(0.1, 0.7) }]
            }
        case "display":
            return {
                base: (ctx, fill) => {
                    ctx.beginPath(); ctx.roundedRect(3, 4.5, 18, 12, 2, 2); fill(); ctx.stroke()
                    ctx.beginPath(); ctx.moveTo(12, 16.5); ctx.lineTo(12, 19.5); ctx.moveTo(8, 19.5); ctx.lineTo(16, 19.5); ctx.stroke()
                },
                // A line sweeping down the screen once.
                layers: [{ draw: (ctx) => { ctx.lineWidth = thin; ctx.globalAlpha = 0.6; ctx.beginPath(); ctx.moveTo(6, 7); ctx.lineTo(18, 7); ctx.stroke() },
                           dy: () => 7 * phase, opacity: () => bump(0, 1) }]
            }
        case "sound":
            return {
                base: (ctx, fill) => {
                    ctx.beginPath()
                    ctx.moveTo(3.5, 9.2); ctx.lineTo(7.4, 9.2); ctx.lineTo(12, 5.2); ctx.lineTo(12, 18.8); ctx.lineTo(7.4, 14.8); ctx.lineTo(3.5, 14.8)
                    ctx.closePath(); fill(); ctx.stroke()
                },
                layers: [{ draw: (ctx) => { ctx.beginPath(); ctx.arc(12.5, 12, 4.2, -0.75, 0.75); ctx.stroke() },
                           opacity: () => 1 - 0.65 * bump(0, 0.55) },
                         { draw: (ctx) => { ctx.beginPath(); ctx.arc(12.5, 12, 7.8, -0.8, 0.8); ctx.stroke() },
                           opacity: () => 1 - 0.65 * bump(0.3, 0.6) }]
            }
        case "appearance":
            return {
                base: (ctx) => { circle(ctx, 12, 12, 8.8); ctx.stroke() },
                layers: [{ draw: (ctx) => { ctx.fillStyle = ctx.strokeStyle; ctx.beginPath(); ctx.arc(12, 12, 8.8, -Math.PI / 2, Math.PI / 2); ctx.closePath(); ctx.fill() },
                           rotation: () => 180 * turns }]
            }
        case "language":
            return {
                base: (ctx, fill) => {
                    circle(ctx, 12, 12, 8.8); fill(); ctx.stroke()
                    ctx.beginPath(); ctx.moveTo(3.4, 12); ctx.lineTo(20.6, 12); ctx.stroke()
                    ctx.lineWidth = thin
                    ctx.beginPath(); ctx.moveTo(5, 7.6); ctx.lineTo(19, 7.6); ctx.moveTo(5, 16.4); ctx.lineTo(19, 16.4); ctx.stroke()
                },
                // The meridian, narrowing and widening as the globe spins.
                layers: [{ draw: (ctx) => { ctx.beginPath(); ctx.ellipse(8, 3.2, 8, 17.6); ctx.stroke() },
                           xScale: () => Math.abs(Math.cos(Math.PI * turns)) * 0.92 + 0.08 }]
            }
        case "network":
            return {
                base: (ctx) => { ctx.fillStyle = ctx.strokeStyle; circle(ctx, 12, 19.6, 1.45); ctx.fill() },
                layers: [4.4, 8.6, 12.8].map((r, i) => ({
                    draw: (ctx) => { ctx.beginPath(); ctx.arc(12, 19.6, r, Math.PI * 1.25, Math.PI * 1.75); ctx.stroke() },
                    opacity: () => 1 - 0.6 * bump(i * 0.2, 0.55) }))
            }
        case "system":
            return {
                base: (ctx, fill) => {
                    ctx.beginPath(); ctx.roundedRect(3.5, 5.5, 17, 13, 3, 3); fill(); ctx.stroke()
                    ctx.beginPath(); ctx.moveTo(7.5, 10); ctx.lineTo(16.5, 10); ctx.stroke()
                },
                layers: [{ draw: (ctx) => { ctx.fillStyle = ctx.strokeStyle; circle(ctx, 16.3, 14.6, 1.3); ctx.fill() },
                           opacity: () => 1 - 0.75 * Math.max(bump(0, 0.4), bump(0.45, 0.4)) }]
            }
        }
        return { base: (ctx) => {}, layers: [] }
    }

    // A part, painted once: again only for another icon, colour, style, size
    // or stroke.
    component Part: Canvas {
        id: part
        property var draw
        anchors.fill: parent
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            ctx.scale(width / 24, height / 24)
            ctx.strokeStyle = root.colour
            ctx.lineWidth = root.stroke
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            const c = root.colour
            const fill = () => {
                if (root.style !== "duotone")
                    return
                ctx.fillStyle = Qt.rgba(c.r, c.g, c.b, 0.22)
                ctx.fill()
            }
            if (draw)
                draw(ctx, fill)
        }
        Connections {
            target: root
            function onPartsChanged() { part.requestPaint() }
            function onColourChanged() { part.requestPaint() }
            function onStyleChanged() { part.requestPaint() }
            function onSizeChanged() { part.requestPaint() }
            function onStrokeChanged() { part.requestPaint() }
        }
    }

    Part { draw: root.parts.base }
    Repeater {
        model: root.parts.layers.length
        Part {
            required property int index
            readonly property var motion: root.parts.layers[index] || ({})
            draw: motion.draw
            rotation: motion.rotation ? motion.rotation() : 0
            opacity: motion.opacity ? motion.opacity() : 1
            transform: [
                Scale { origin.x: root.size / 2; xScale: motion.xScale ? motion.xScale() : 1 },
                Translate { x: (motion.dx ? motion.dx() : 0) * root.unit; y: (motion.dy ? motion.dy() : 0) * root.unit }
            ]
        }
    }
}
