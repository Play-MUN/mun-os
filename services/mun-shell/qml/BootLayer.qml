import QtQuick
import MUN.Shell
import "Logo.js" as Logo

// The console turning on, once per boot, and off: on a black screen the
// letters appear, the arc draws itself from its tip and the power light comes
// on; two seconds after it began, the screen fades into Home. powerOff()
// brings the black back while the system shuts down.
Rectangle {
    id: root
    // Lit: the logo and the light on. Shown: the layer covers the screen.
    property bool lit: false
    property bool shown: true
    signal finished()

    function start() { lit = true; hideTimer.start() }
    function powerOff() { hideTimer.stop(); lit = false; shown = true }

    anchors.fill: parent
    color: Theme.layer
    opacity: shown ? 1 : 0
    visible: opacity > 0
    Behavior on opacity { NumberAnimation { duration: 500; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }

    Timer {
        id: hideTimer
        interval: 2000
        onTriggered: { root.shown = false; root.finished() }
    }

    // The logo, 420 px wide, and 34 px below it the power light: centred as
    // one block.
    readonly property real logoWidth: 420
    readonly property real logoHeight: logoWidth * 540 / 930
    readonly property real blockTop: (height - (logoHeight + 34 + 14 + 34)) / 2

    Canvas {
        id: logo
        x: (root.width - width) / 2
        y: root.blockTop
        width: root.logoWidth
        height: root.logoHeight
        opacity: root.lit ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 1000; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }

        // How much of the arc shows, as the design's dash: 0 to 800 px along
        // the mask's stroke, whose arc is 771 px long.
        property real reveal: root.lit ? 800 : 0
        Behavior on reveal {
            SequentialAnimation {
                PauseAnimation { duration: 400 }
                NumberAnimation { duration: 1300; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.draw }
            }
        }
        onRevealChanged: requestPaint()

        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            ctx.scale(width / 930, height / 540)
            ctx.translate(-320, -215)
            ctx.fillStyle = Theme.ash
            // The arc shows through a 52 px round-capped stroke along the SVG
            // arc M769.7 251.6 A417 417 0 0 1 1215.3 746.6 (centre 805.96,
            // 667.02, from -1.6578 rad clockwise), used as a clip: the
            // stroke's outline is two arcs joined by the caps' half circles.
            if (reveal > 0.5) {
                const cx = 805.96, cy = 667.02, r = 417, half = 26
                const from = -1.6578, to = from + Math.min(reveal, 771.4) / r
                ctx.save()
                ctx.beginPath()
                ctx.arc(cx, cy, r + half, from, to, false)
                ctx.arc(cx + r * Math.cos(to), cy + r * Math.sin(to), half, to, to + Math.PI, false)
                ctx.arc(cx, cy, r - half, to, from, true)
                ctx.arc(cx + r * Math.cos(from), cy + r * Math.sin(from), half, from + Math.PI, from + 2 * Math.PI, false)
                ctx.closePath()
                ctx.clip()
                ctx.path = Logo.arc
                ctx.fill()
                ctx.restore()
            }
            ctx.fillRule = Qt.OddEvenFill
            ctx.path = Logo.letters
            ctx.fill()
        }
    }

    Rectangle {
        id: light
        x: (root.width - width) / 2
        y: root.blockTop + root.logoHeight + 34
        width: 14
        height: 14
        radius: 7
        color: root.lit ? Theme.led : Theme.ledOff
        Behavior on color { ColorAnimation { duration: 300; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }
        Shadow {
            z: -1
            radii: [7]
            shadows: [{ blur: 14, spread: 3, color: Theme.rgba(221, 233, 255, 0.9) }]
            opacity: root.lit ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }
        }
    }
}
