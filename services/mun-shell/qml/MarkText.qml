import QtQuick
import MUN.Shell

// Michroma, the mark's face, for short upper-case labels: kickers, section
// heads, the path at the bottom and the clock. `tracking` is CSS
// letter-spacing in em.
Text {
    property int size: 15
    property real tracking: 0.24

    textFormat: Text.PlainText
    color: Theme.ash
    font.family: Theme.mark
    font.pixelSize: size
    font.letterSpacing: tracking * size
}
