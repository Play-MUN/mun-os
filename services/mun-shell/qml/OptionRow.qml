import QtQuick
import MUN.Shell

// A row of options: a label and, on the right, its value. A choice shows
// arrows around the value while it has the focus (Left and Right change it).
// `large` is a dialog's button size.
Item {
    id: root
    property string label
    property string value
    property bool choice: false
    property bool on: false
    property bool large: false

    readonly property int padX: large ? 24 : 22
    readonly property int padY: large ? 14 : 10
    implicitHeight: Math.max(large ? 66 : 56, 2 * padY + Math.max(labelText.height, valueRow.height))

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: root.on ? Theme.accentDeepAlpha(0.12) : Theme.rgba(255, 255, 255, 0.035)
        border.width: root.on ? 2 : 1
        border.color: root.on ? Theme.accent : Theme.rgba(255, 255, 255, 0.06)
    }
    UiText {
        id: labelText
        x: root.padX
        width: Math.min(implicitWidth, root.width - 2 * root.padX - valueRow.width - 24)
        anchors.verticalCenter: parent.verticalCenter
        text: root.label
        size: root.large ? 26 : 24
        elide: Text.ElideRight
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
            color: root.on ? Theme.moon : Theme.ash
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
