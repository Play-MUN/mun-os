import QtQuick
import MUN.Shell

// A dressed surface's plate (docs/shape.md, "Contrast"): the game's plate
// colour in its material, drawn at the opacity the contrast rule gave (the
// item's `opacity`, set by its user). `solid` is the plain colour; `glass`
// adds a sheen that lightens its top by up to `sheen`; `paper` a grain of up
// to `grain` either way, lighter above and darker below. The contrast proofs
// include exactly those ranges; nothing here goes beyond them. `amount`
// scales the sheen or the grain: while a transition blends a plate from MUN's
// (plain) into the game's, the material comes in with the colour, so every
// frame stays within the range of the two ends.
Item {
    id: root
    property color plate
    property string material: "solid"
    property var radii: [0]
    property real amount: 1
    readonly property real sheen: 0.06
    readonly property real grain: 0.04

    Box {
        anchors.fill: parent
        radii: root.radii
        color: root.plate
    }
    Box {
        anchors.fill: parent
        visible: root.material === "glass" && root.amount > 0
        radii: root.radii
        gradient: ({ type: "linear", angle: 180, stops: [[0, Theme.rgba(255, 255, 255, root.sheen * root.amount)], [0.55, Theme.rgba(255, 255, 255, 0)]] })
    }
    Box {
        anchors.fill: parent
        visible: root.material === "paper" && root.amount > 0
        radii: root.radii
        gradient: ({ type: "linear", angle: 180, stops: [[0, Theme.rgba(255, 255, 255, root.grain * root.amount)], [0.5, Theme.rgba(255, 255, 255, 0)], [1, Theme.rgba(0, 0, 0, root.grain * root.amount)]] })
    }
}
