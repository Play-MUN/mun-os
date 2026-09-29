import QtQuick
import MUN.Shell

// Tonight's moon, drawn from the date: the lit part of a 16 px disc in a
// 20-unit box, as the design's icon. `phase` is 0 at new moon and 0.5 at full.
Canvas {
    id: root
    property real phase: 0
    implicitWidth: 24
    implicitHeight: 24
    onPhaseChanged: requestPaint()

    // Mean synodic month from the new moon of 6 January 2000, 18:14 UTC.
    function phaseAt(date) {
        const synodic = 29.530588853
        const reference = Date.UTC(2000, 0, 6, 18, 14) / 864e5
        return (((date.getTime() / 864e5 - reference) % synodic) + synodic) % synodic / synodic
    }

    onPaint: {
        const ctx = getContext("2d")
        ctx.reset()
        ctx.scale(width / 20, height / 20)
        ctx.fillStyle = Theme.moonShadow
        ctx.beginPath()
        ctx.arc(10, 10, 8, 0, 2 * Math.PI)
        ctx.fill()

        const p = root.phase
        const lit = p <= 0.5 ? p * 2 : (1 - p) * 2
        if (lit < 0.02)
            return
        ctx.fillStyle = Theme.moon
        ctx.beginPath()
        if (lit > 0.98) {
            ctx.arc(10, 10, 8, 0, 2 * Math.PI)
            ctx.fill()
            return
        }
        // Waxing, the right side is lit; the terminator is half an ellipse,
        // bulging into the dark side past the first quarter.
        const waxing = p < 0.5
        const rx = Math.abs(1 - 2 * lit) * 8
        ctx.arc(10, 10, 8, -Math.PI / 2, Math.PI / 2, !waxing)
        const throughLeft = (lit > 0.5) === waxing
        const steps = 24
        for (let i = 1; i <= steps; ++i) {
            const th = Math.PI / 2 + (throughLeft ? 1 : -1) * Math.PI * i / steps
            ctx.lineTo(10 + rx * Math.cos(th), 10 + 8 * Math.sin(th))
        }
        ctx.closePath()
        ctx.fill()
    }
}
