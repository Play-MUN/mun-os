import QtQuick
import MUN.Shell

// Text in Archivo, the design's type: `weight` and `stretch` are the CSS
// font-weight and font-stretch, set on the font's own axes. `lineHeight` as a
// CSS multiple (0 keeps the font's natural line) spaces the lines as CSS
// does: the line box's height is the multiple and the glyphs sit centred in
// it.
//
// Always plain text: a card's title comes from its manifest and is never
// interpreted as markup.
Text {
    id: root
    property int size: 24
    property int weight: 400
    property int stretch: 100
    property real tracking: 0        // CSS letter-spacing, in em
    property real cssLineHeight: 0

    // Archivo's ascent plus descent, per pixel of size.
    readonly property real naturalLine: 1.088 * size

    textFormat: Text.PlainText
    color: Theme.moon
    font.family: Theme.sans
    font.pixelSize: size
    font.variableAxes: ({ "wght": root.weight, "wdth": root.stretch })
    font.letterSpacing: tracking * size
    lineHeightMode: cssLineHeight > 0 ? Text.FixedHeight : Text.ProportionalHeight
    lineHeight: cssLineHeight > 0 ? cssLineHeight * size : 1
    topPadding: cssLineHeight > 0 ? (cssLineHeight * size - naturalLine) / 2 : 0
    height: cssLineHeight > 0 ? lineCount * cssLineHeight * size : implicitHeight
}
