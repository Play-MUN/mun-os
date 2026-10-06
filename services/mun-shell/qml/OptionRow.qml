import QtQuick
import MUN.Shell

// A row of options: a label and, on the right, its value. A choice shows
// arrows around the value while it has the focus (Left and Right change it).
// `large` is a dialog's button size. In the game's dressed panel (`shaped`)
// the row draws no fill of its own over the game's plate, only its frame in
// the focus colour, and its text in the plate's text colour (`textColour`);
// `focusColour` is the panel's (MUN's copper everywhere else).
Item {
    id: root
    property string label
    property string value
    property bool choice: false
    property bool on: false
    property bool large: false
    property bool shaped: false
    property color focusColour: Theme.accent
    property color textColour: Shape.text

    readonly property int padX: large ? 24 : 22
    readonly property int padY: large ? 14 : 10
    implicitHeight: Math.max(large ? 66 : 56, 2 * padY + Math.max(labelText.height, valueRow.height))

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: root.shaped ? "transparent" : root.on ? Theme.alpha(root.focusColour, 0.12) : Theme.rgba(255, 255, 255, 0.035)
        border.width: root.on ? 2 : 1
        border.color: root.on ? root.focusColour : root.shaped ? Theme.alpha(root.textColour, 0.25) : Theme.rgba(255, 255, 255, 0.06)
    }
    UiText {
        id: labelText
        x: root.padX
        width: Math.min(implicitWidth, root.width - 2 * root.padX - valueRow.width - 24)
        anchors.verticalCenter: parent.verticalCenter
        text: root.label
        size: root.large ? 26 : 24
        elide: Text.ElideRight
        color: root.shaped ? root.textColour : Theme.moon
    }
    Row {
        id: valueRow
        anchors.right: parent.right
        anchors.rightMargin: root.padX
        anchors.verticalCenter: parent.verticalCenter
        spacing: 14
        UiText {
            visible: root.choice
            anchors.verticalCenter: parent.verticalCenter
            text: "‹"
            size: 20
            color: valueText.color
            opacity: root.on ? 0.8 : 0
        }
        UiText {
            id: valueText
            visible: text !== ""
            anchors.verticalCenter: parent.verticalCenter
            text: root.value
            size: 23
            color: root.shaped ? root.textColour : root.on ? Theme.moon : Theme.ash
        }
        UiText {
            visible: root.choice
            anchors.verticalCenter: parent.verticalCenter
            text: "›"
            size: 20
            color: valueText.color
            opacity: root.on ? 0.8 : 0
        }
    }
}
