import QtQuick
import MUN.Shell
import "Logo.js" as Logo
import "PlayMun.js" as PlayMun

// The console turning on, once per boot, over the start-up sound
// (sounds/startup.wav, 10.1 s), in two acts; and off.
//
// The house: an ivory dawn, and on it PLAY, then MUN, then the copper stroke
// under them, each arriving with one of the sound's three notes (2.30, 3.24
// and 3.86 s into it), then a sheen across the letters. The console: the
// night, the MUN letters written and filled, the moon's arc drawing itself
// from its tip with the sound's last breath (6.74 s), stars drifting. The
// console stays while its services come up: Home follows once the sound has
// played out (9.3 s) and both the card service and the launcher have
// answered, or kReady after the start whatever they do. Any key skips to
// the console. powerOff() brings the black back while the system shuts down.
//
// Everything is drawn from one clock, t, in seconds of the sound. It starts
// when the sound's first samples reach the device, less what the device
// holds ahead (SystemSounds.latency), so the picture follows what is heard;
// with the interface sounds off, or no device, it starts on its own. The
// logos are painted once; only the sheen, the letters being written and the
// arc repaint, and only while they move.
Rectangle {
    id: root
    // Shown: the layer covers the screen.
    property bool shown: true
    // Seconds into the start-up sound.
    property real t: 0
    // The start-up is under way (keys skip it), or held for the services.
    readonly property bool playing: clock.running || waiting.running || holding
    property bool holding: false
    property bool late: false
    signal finished()

    readonly property real handOver: 9.3
    readonly property real skipTo: 8.5
    readonly property int kReady: 20000
    readonly property bool servicesReady: CardClient.readerAvailable && LaunchClient.available

    // The Play MUN logo's own colours.
    readonly property color ink: "#111113"
    readonly property color brushCopper: "#A8764F"
    readonly property color moonlight: "#ECE9E3"

    function start() {
        holding = false
        late = false
        ready.restart()
        if (ShellSettings.systemSounds) {
            SystemSounds.play("startup")
            waiting.restart()
        } else {
            begin()
        }
    }
    function begin() {
        waiting.stop()
        clock.stop()
        t = -SystemSounds.latency / 1000
        clock.from = t
        clock.duration = (handOver - t) * 1000
        clock.start()
    }
    function skip() {
        if (!playing || t >= skipTo && holding)
            return
        SystemSounds.stop("startup")
        waiting.stop()
        clock.stop()
        t = Math.max(t, skipTo)
        holding = true
        handOverIfReady()
    }
    function handOverIfReady() {
        if (holding && (servicesReady || late)) {
            holding = false
            ready.stop()
            shown = false
            finished()
        }
    }
    function powerOff() {
        waiting.stop()
        clock.stop()
        ready.stop()
        holding = false
        t = 0
        shown = true
    }

    onServicesReadyChanged: handOverIfReady()

    // Waiting for the sound's first samples; without them after a second
    // (no device), the picture goes on alone.
    Timer { id: waiting; interval: 1000; onTriggered: root.begin() }
    Timer { id: ready; interval: root.kReady; onTriggered: { root.late = true; root.handOverIfReady() } }
    NumberAnimation {
        id: clock
        target: root
        property: "t"
        to: root.handOver
        onFinished: { root.holding = true; root.handOverIfReady() }
    }
    Connections {
        target: SystemSounds
        // The sound started after the picture had (a slow device): start
        // again with it, while the screen is still black.
        function onStarted(name) {
            if (name === "startup" && (waiting.running || clock.running && root.t < 0.45))
                root.begin()
        }
    }

    function clamp(v) { return Math.max(0, Math.min(1, v)) }
    function span(a, b) { return clamp((t - a) / (b - a)) }
    function ease(u) { return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2 }
    function out3(u) { return 1 - Math.pow(1 - u, 3) }

    anchors.fill: parent
    color: Theme.layer
    opacity: shown ? 1 : 0
    visible: opacity > 0
    Behavior on opacity { NumberAnimation { duration: 500; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }

    // ------------------------------------------------------------ the house

    Item {
        id: house
        anchors.fill: parent
        opacity: root.ease(root.span(0.5, 2.2)) * (1 - root.ease(root.span(5.0, 5.9)))

        Canvas {
            id: dawn
            anchors.fill: parent
            // radial-gradient(90% 90% at 50% 45%): an ellipse, drawn as a
            // circle stretched to the screen's proportions.
            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                const rx = width * 0.9, ry = height * 0.9
                ctx.translate(width * 0.5, height * 0.45)
                ctx.scale(1, ry / rx)
                const g = ctx.createRadialGradient(0, 0, 0, 0, 0, rx)
                g.addColorStop(0, "#FBFAF6")
                g.addColorStop(0.55, "#F6F4EE")
                g.addColorStop(1, "#E7E3DA")
                ctx.fillStyle = g
                ctx.fillRect(-rx, -rx, 2 * rx, 2 * rx)
            }
        }

        // The logo's 990 x 320 box, 1150 px wide, at the centre.
        Item {
            id: mark
            readonly property real k: 1150 / 990
            width: 990 * k
            height: 320 * k
            anchors.centerIn: parent

            Canvas {
                id: play
                anchors.fill: parent
                opacity: root.out3(root.span(2.30, 3.10))
                transform: Translate { y: 10 * (1 - play.opacity) }
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    ctx.scale(mark.k, mark.k)
                    ctx.translate(-300, -330)
                    ctx.fillRule = Qt.OddEvenFill
                    ctx.fillStyle = root.ink
                    ctx.path = PlayMun.play
                    ctx.fill()
                }
            }
            Canvas {
                id: letters
                anchors.fill: parent
                opacity: root.out3(root.span(3.24, 4.04))
                transform: Scale {
                    origin.x: letters.width / 2
                    origin.y: letters.height * 0.6
                    xScale: 1.035 - 0.035 * letters.opacity
                    yScale: xScale
                }
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    ctx.scale(mark.k, mark.k)
                    ctx.translate(-300, -330)
                    ctx.fillRule = Qt.OddEvenFill
                    ctx.fillStyle = root.ink
                    ctx.path = PlayMun.letters
                    ctx.fill()
                }
            }
            // The copper stroke, brushed from the left with the chime.
            Item {
                id: brush
                x: (677.5 - 300) * mark.k
                y: (591 - 330) * mark.k
                width: 231.5 * mark.k * root.ease(root.span(3.855, 4.40))
                height: 27 * mark.k
                clip: true
                Canvas {
                    x: -brush.x
                    y: -brush.y
                    width: mark.width
                    height: mark.height
                    onPaint: {
                        const ctx = getContext("2d")
                        ctx.reset()
                        ctx.scale(mark.k, mark.k)
                        ctx.translate(-300, -330)
                        ctx.fillStyle = root.brushCopper
                        ctx.path = PlayMun.stroke
                        ctx.fill()
                    }
                }
            }
            // A band of light across the letters, through their shapes.
            Canvas {
                id: sheen
                anchors.fill: parent
                readonly property real at: root.span(4.34, 4.94)
                opacity: at > 0 && at < 1 ? 0.9 : 0
                onAtChanged: if (at > 0 && at < 1) requestPaint()
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    ctx.scale(mark.k, mark.k)
                    ctx.translate(-300, -330)
                    ctx.fillRule = Qt.OddEvenFill
                    ctx.path = PlayMun.play + " " + PlayMun.letters
                    ctx.clip()
                    ctx.transform(1, 0, Math.tan(-18 * Math.PI / 180), 1, 0, 0)
                    const x = 240 + 1150 * at
                    const g = ctx.createLinearGradient(x, 0, x + 160, 0)
                    g.addColorStop(0, "rgba(255, 255, 255, 0)")
                    g.addColorStop(0.5, "rgba(255, 255, 255, 0.85)")
                    g.addColorStop(1, "rgba(255, 255, 255, 0)")
                    ctx.fillStyle = g
                    ctx.fillRect(x, 330, 160, 320)
                }
            }
        }
    }

    // ------------------------------------------------------------ the console

    Item {
        id: night
        anchors.fill: parent
        opacity: root.ease(root.span(5.4, 6.0))

        Canvas {
            anchors.fill: parent
            // radial-gradient(70% 80% at 58% 42%).
            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                const rx = width * 0.7, ry = height * 0.8
                ctx.translate(width * 0.58, height * 0.42)
                ctx.scale(1, ry / rx)
                const g = ctx.createRadialGradient(0, 0, 0, 0, 0, rx)
                g.addColorStop(0, "#0E1014")
                g.addColorStop(0.6, "#050608")
                g.addColorStop(1, "#000000")
                ctx.fillStyle = g
                ctx.fillRect(-2 * rx, -2 * rx, 4 * rx, 4 * rx)
            }
        }

        // Moon dust: ninety specks rising slowly, each at its own pace.
        Item {
            id: stars
            anchors.fill: parent
            opacity: root.span(5.6, 7.0)
            property real drift: 0
            NumberAnimation on drift {
                from: 0
                to: 3600
                duration: 3600 * 1000
                running: root.visible && night.opacity > 0
            }
            readonly property var motes: {
                const list = []
                for (let i = 0; i < 90; ++i)
                    list.push({ x: Math.random() * 1920, y: Math.random() * 1080, r: Math.random() * 1.4 + 0.3,
                                v: Math.random() * 6 + 2, a: Math.random() * 0.5 + 0.15, w: Math.random() * 6.28 })
                return list
            }
            Repeater {
                model: stars.motes.length
                Rectangle {
                    required property int index
                    readonly property var mote: stars.motes[index]
                    x: mote.x + 3 * Math.sin(stars.drift * 0.08 + mote.w) - mote.r
                    y: ((mote.y - mote.v * stars.drift) % 1090 + 1090) % 1090 - 5
                    width: 2 * mote.r
                    height: width
                    radius: mote.r
                    color: Qt.rgba(221 / 255, 233 / 255, 1, mote.a)
                }
            }
        }

        // The console's logo: its 930 x 540 box (Logo.js), 1016 px wide,
        // where the design's 970 x 570 view, 1060 px wide, puts it.
        Item {
            id: console_
            readonly property real k: 1060 / 970
            x: (root.width - 970 * k) / 2 + (320 - 300) * k
            y: (root.height - 570 * k) / 2 + (215 - 200) * k
            width: 930 * k
            height: 540 * k

            // The moon's light behind it, breathing once it is lit.
            Canvas {
                readonly property real r: 523.1 * console_.k
                x: (805.9 - 320) * console_.k - r
                y: (607.0 - 215) * console_.k - r
                width: 2 * r
                height: 2 * r
                opacity: root.ease(root.span(6.74, 7.5)) * (0.9 + 0.1 * Math.sin(stars.drift * 2))
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    const g = ctx.createRadialGradient(r, r, 0, r, r, r)
                    g.addColorStop(0, "rgba(221, 233, 255, 0.16)")
                    g.addColorStop(1, "rgba(221, 233, 255, 0)")
                    ctx.fillStyle = g
                    ctx.fillRect(0, 0, 2 * r, 2 * r)
                }
            }

            // The letters' outlines, written from each outline's start, all
            // finishing together; they fade to a trace under the fill.
            Canvas {
                id: writing
                anchors.fill: parent
                readonly property real written: root.ease(root.span(5.57, 6.87))
                opacity: 1 - 0.75 * root.span(7.0, 7.8)
                onWrittenChanged: requestPaint()
                // Logo.letters flattened once: each outline a list of points
                // with the length along it.
                readonly property var outlines: {
                    const list = []
                    const nums = s => s.trim().split(/[\s,]+/).map(Number)
                    let outline = null, x = 0, y = 0
                    const to = (nx, ny) => {
                        const last = outline.points[outline.points.length - 1]
                        outline.length += Math.hypot(nx - last.x, ny - last.y)
                        outline.points.push({ x: nx, y: ny, at: outline.length })
                        x = nx; y = ny
                    }
                    for (const command of Logo.letters.match(/[MCLZ][^MCLZ]*/g)) {
                        const v = nums(command.slice(1) || "0")
                        switch (command[0]) {
                        case "M":
                            outline = { points: [{ x: v[0], y: v[1], at: 0 }], length: 0 }
                            list.push(outline)
                            x = v[0]; y = v[1]
                            break
                        case "L":
                            to(v[0], v[1])
                            break
                        case "C":
                            for (let i = 1, x0 = x, y0 = y; i <= 12; ++i) {
                                const s = i / 12, u = 1 - s
                                to(u * u * u * x0 + 3 * u * u * s * v[0] + 3 * u * s * s * v[2] + s * s * s * v[4],
                                   u * u * u * y0 + 3 * u * u * s * v[1] + 3 * u * s * s * v[3] + s * s * s * v[5])
                            }
                            break
                        case "Z":
                            to(outline.points[0].x, outline.points[0].y)
                            break
                        }
                    }
                    return list
                }
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    if (written <= 0)
                        return
                    ctx.scale(console_.k, console_.k)
                    ctx.translate(-320, -215)
                    ctx.strokeStyle = root.moonlight
                    ctx.lineWidth = 2.2
                    ctx.lineJoin = "round"
                    ctx.lineCap = "round"
                    ctx.beginPath()
                    for (const outline of outlines) {
                        const reach = outline.length * written
                        const points = outline.points
                        ctx.moveTo(points[0].x, points[0].y)
                        for (let i = 1; i < points.length; ++i) {
                            const p = points[i]
                            if (p.at <= reach) {
                                ctx.lineTo(p.x, p.y)
                                continue
                            }
                            const q = points[i - 1], f = (reach - q.at) / (p.at - q.at)
                            ctx.lineTo(q.x + (p.x - q.x) * f, q.y + (p.y - q.y) * f)
                            break
                        }
                    }
                    ctx.stroke()
                }
            }
            Canvas {
                anchors.fill: parent
                opacity: root.ease(root.span(6.5, 7.3))
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    ctx.scale(console_.k, console_.k)
                    ctx.translate(-320, -215)
                    ctx.fillRule = Qt.OddEvenFill
                    ctx.fillStyle = root.moonlight
                    ctx.path = Logo.letters
                    ctx.fill()
                }
            }
            // The arc, drawn from its tip.
            Canvas {
                anchors.fill: parent
                // How much of the arc shows, as the design's dash: 0 to 800 px
                // along the mask's stroke, whose arc is 771 px long.
                readonly property real reveal: 800 * root.ease(root.span(6.15, 7.2))
                onRevealChanged: requestPaint()
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    if (reveal <= 0.5)
                        return
                    ctx.scale(console_.k, console_.k)
                    ctx.translate(-320, -215)
                    ctx.fillStyle = root.moonlight
                    // The arc shows through a 52 px round-capped stroke along
                    // the SVG arc M769.7 251.6 A417 417 0 0 1 1215.3 746.6
                    // (centre 805.96, 667.02, from -1.6578 rad clockwise),
                    // used as a clip: the stroke's outline is two arcs joined
                    // by the caps' half circles.
                    const cx = 805.96, cy = 667.02, r = 417, half = 26
                    const from = -1.6578, to = from + Math.min(reveal, 771.4) / r
                    ctx.beginPath()
                    ctx.arc(cx, cy, r + half, from, to, false)
                    ctx.arc(cx + r * Math.cos(to), cy + r * Math.sin(to), half, to, to + Math.PI, false)
                    ctx.arc(cx, cy, r - half, to, from, true)
                    ctx.arc(cx + r * Math.cos(from), cy + r * Math.sin(from), half, from + Math.PI, from + 2 * Math.PI, false)
                    ctx.closePath()
                    ctx.clip()
                    ctx.path = Logo.arc
                    ctx.fill()
                }
            }
        }
    }
}
