import QtQuick
import MUN.Shell

// A dressed surface's plate (docs/shape.md, "Contrast"): the game's plate
// colour in its material, drawn at the opacity the contrast rule gave (the
// item's `opacity`, set by its user). `solid` is the plain colour; `glass`
// adds a sheen that lightens its top by up to 6 %; `paper` a grain of up to
// 4 % either way, lighter above and darker below. The contrast proofs
// include exactly those ranges; nothing here goes beyond them.
Item {
    id: root
    property color plate
    property string material: "solid"
    property var radii: [0]

    Box {
        anchors.fill: parent
        radii: root.radii
        color: root.plate
    }
    Box {
        anchors.fill: parent
        visible: root.material === "glass"
        radii: root.radii
        gradient: ({ type: "linear", angle: 180, stops: [[0, Theme.rgba(255, 255, 255, 0.06)], [0.55, Theme.rgba(255, 255, 255, 0)]] })
    }
    Box {
        anchors.fill: parent
        visible: root.material === "paper"
        radii: root.radii
        gradient: ({ type: "linear", angle: 180, stops: [[0, Theme.rgba(255, 255, 255, 0.04)], [0.5, Theme.rgba(255, 255, 255, 0)], [1, Theme.rgba(0, 0, 0, 0.04)]] })
    }
}
