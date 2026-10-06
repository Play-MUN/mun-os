pragma Singleton
import QtQuick
import MUN.Shell

// The design's tokens, in its own units: logical pixels of the 1920x1080
// canvas and milliseconds. Colour names follow the design's CSS variables
// (--noche night, --luna moon, --ceniza ash, --cobre copper, --patina patina,
// --azul blue, --led led).
QtObject {
    readonly property color night: "#121317"
    readonly property color moon: "#DAD7D1"
    readonly property color ash: "#8F8C87"
    readonly property color line: rgba(218, 215, 209, 0.16)
    readonly property color copper: "#C27B48"
    readonly property color copperLight: "#E39A63"
    readonly property color led: "#DDE9FF"
    readonly property color blue: "#3E5C82"
    readonly property color blueLight: "#8FB0D6"
    readonly property color patina: "#7FB3A3"
    readonly property color patinaDark: "#4E7F72"
    readonly property color black: "#07080A"
    // Surfaces and type outside the palette.
    readonly property color screen: "#08090B"        // behind everything
    readonly property color layer: "#050506"         // start-up, power-off and game hand-over
    readonly property color dialog: "#17181C"
    readonly property color inkOnMoon: "#131417"     // a chosen entry's label
    readonly property color ashOnMoon: "#4A4844"     // a chosen entry's detail
    readonly property color dim: "#6E6C68"           // text on the hand-over layer
    readonly property color unlit: "#50535B"         // a status light that is off
    readonly property color ledOff: "#2E3035"
    readonly property color moonShadow: "#3A3B41"

    // MUN's focus: a chosen entry's ring, dot, wire and edge, a chosen
    // option's frame. It is the same everywhere MUN speaks (Settings, their
    // panels, every dialog); a Game Card's colours, lent or from its MUN
    // Shape, reach only the eligible surfaces, through the Shape singleton
    // (src/shape.h, docs/shape.md).
    readonly property color accent: copperLight
    readonly property color accentDeep: copper
    function accentAlpha(a) { return Qt.rgba(accent.r, accent.g, accent.b, a) }
    function accentDeepAlpha(a) { return Qt.rgba(accentDeep.r, accentDeep.g, accentDeep.b, a) }
    // A colour at an alpha.
    function alpha(colour, a) { return Qt.rgba(colour.r, colour.g, colour.b, a) }

    readonly property string sans: "Archivo"
    readonly property string mark: "Michroma"

    // The design's canvas and the orb its menus turn around.
    readonly property int canvasWidth: 1920
    readonly property int canvasHeight: 1080
    readonly property point orb: Qt.point(470, 570)
    readonly property int gutter: 96

    // Motion: the design's CSS timing functions as Qt bezier splines.
    readonly property var ease: [0.25, 0.1, 0.25, 1, 1, 1]          // CSS ease, the default
    readonly property var settle: [0.2, 0.8, 0.2, 1, 1, 1]          // cubic-bezier(.2,.8,.2,1)
    readonly property var draw: [0.65, 0, 0.3, 1, 1, 1]             // the start-up arc

    function rgba(r, g, b, a) { return Qt.rgba(r / 255, g / 255, b / 255, a) }

    // CSS `perspective(distance) rotateY(degrees)` around (ox, oy), as a 4x4
    // matrix (for ProjectedView; the far side really narrows).
    function perspectiveY(degrees, distance, ox, oy) {
        const a = degrees * Math.PI / 180, c = Math.cos(a), s = Math.sin(a)
        const toOrigin = Qt.matrix4x4(1, 0, 0, ox, 0, 1, 0, oy, 0, 0, 1, 0, 0, 0, 0, 1)
        const project = Qt.matrix4x4(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -1 / distance, 1)
        const turn = Qt.matrix4x4(c, 0, s, 0, 0, 1, 0, 0, -s, 0, c, 0, 0, 0, 0, 1)
        const fromOrigin = Qt.matrix4x4(1, 0, 0, -ox, 0, 1, 0, -oy, 0, 0, 1, 0, 0, 0, 0, 1)
        return toOrigin.times(project).times(turn).times(fromOrigin)
    }

    // The menu arcs lean back 10 degrees from the left edge of the canvas
    // (CSS perspective(2200px) rotateY(10deg) around 0 55%): that
    // perspective in the coordinates of an entry at (x, y) of the arcs'
    // plane, moved `shift` along it.
    function lean(x, y, shift) {
        const toEntry = Qt.matrix4x4(1, 0, 0, -x, 0, 1, 0, -y, 0, 0, 1, 0, 0, 0, 0, 1)
        const fromEntry = Qt.matrix4x4(1, 0, 0, x + shift, 0, 1, 0, y, 0, 0, 1, 0, 0, 0, 0, 1)
        return toEntry.times(perspectiveY(10, 2200, 0, canvasHeight * 0.55)).times(fromEntry)
    }
}
