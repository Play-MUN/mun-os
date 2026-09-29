import QtQuick
import MUN.Shell

// The outer box shadows of its parent, placed around it: declare it inside
// the item that casts it, with z: -1 so it is painted beneath. `shadows` and
// `radii` are as in CSS (see BoxShadow).
BoxShadow {
    boxWidth: parent ? parent.width : 0
    boxHeight: parent ? parent.height : 0
    x: -margin
    y: -margin
    width: boxWidth + 2 * margin
    height: boxHeight + 2 * margin
}
