import QtQuick
import MUN.Shell

// A question or a notice over everything: a title, a line of text, optional
// facts (one per line) and its choices, the first chosen. Main moves the
// choice and runs it; B closes.
//
// `content` is {title, text, facts, actions: [{label, value, run}]}; `text`
// may be a function, for a line that changes while it is shown (a
// countdown), and an action's optional `value` is shown on its right.
Rectangle {
    id: root
    property var content: null
    property int selected: 0
    // A pointer chose answer `index`.
    signal chosen(int index)
    readonly property bool shown: content !== null
    readonly property var actions: content ? content.actions || [] : []

    anchors.fill: parent
    color: Theme.rgba(8, 9, 11, 0.72)
    opacity: shown ? 1 : 0
    visible: opacity > 0
    Behavior on opacity { NumberAnimation { duration: 500; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }
    MouseArea { anchors.fill: parent; enabled: root.shown }

    // What it last showed stays while it fades out.
    property var shownContent: ({ title: "", text: "", facts: [], actions: [] })
    onContentChanged: if (content) shownContent = content

    Rectangle {
        anchors.centerIn: parent
        width: 760
        height: box.height + 96
        radius: 24
        color: Theme.dialog
        border.width: 1
        border.color: Theme.rgba(255, 255, 255, 0.08)

        Column {
            id: box
            x: 48
            y: 48
            width: parent.width - 96
            UiText {
                width: parent.width
                text: root.shownContent.title || ""
                size: 40
                weight: 700
                stretch: 118
                wrapMode: Text.WordWrap
            }
            Item { width: 1; height: 14 }
            UiText {
                width: parent.width
                text: {
                    const line = root.shownContent.text
                    return typeof line === "function" ? line() : line || ""
                }
                size: 24
                color: Theme.ash
                wrapMode: Text.WordWrap
            }
            Item { width: 1; height: facts.count > 0 ? 18 : 0 }
            Repeater {
                id: facts
                model: root.shownContent.facts || []
                UiText {
                    required property string modelData
                    width: box.width
                    text: modelData
                    size: 21
                    color: Theme.ash
                    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                }
            }
            Item { width: 1; height: 28 }
            Column {
                width: parent.width
                spacing: 8
                Repeater {
                    model: (root.shownContent.actions || []).length
                    OptionRow {
                        required property int index
                        width: box.width
                        height: implicitHeight
                        large: true
                        label: root.shownContent.actions[index] ? root.shownContent.actions[index].label : ""
                        value: root.shownContent.actions[index] ? root.shownContent.actions[index].value || "" : ""
                        on: index === root.selected
                        MouseArea {
                            anchors.fill: parent
                            enabled: root.shown
                            onClicked: root.chosen(index)
                        }
                    }
                }
            }
        }
    }
}
