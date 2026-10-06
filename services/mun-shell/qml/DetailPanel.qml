import QtQuick
import MUN.Shell

// The panel on the right: what the entry in focus is, and its options. It
// leans back 16 degrees from its right edge and stays centred on the orb's
// height as its content grows, sliding there. Up to 560 px of options show
// at once; beyond that the list scrolls to keep the chosen one in view and
// fades out at the bottom. It is laid out flat and shown projected
// (ProjectedLayer).
//
// `content` is {kicker, title, text, options}; an option is
// {kind: "head" | "choice" | "action" | "info", label, value}. `selected` is
// the chosen option's index, -1 while the options do not have the focus.
//
// `dressed` is the game's panel (the Game Card entry's): it takes the active
// card's identity (Shape, docs/shape.md), the game's plate, text and focus
// while its colours hold (MUN's set on a plate of its proven opacity where
// only its world is drawn), else MUN's look with the card's focus colour.
// The identity arrives as the transition's front reaches the panel
// (`reached`), its plate blending by its proven plan. Every other panel is
// MUN's; over a game's world (`overWorld`: Home with a world drawn) it sits
// on MUN's own plate at its proven opacity, from the moment the front
// reaches it, every text in MUN's text colour.
ProjectedLayer {
    id: root
    property var content: ({ kicker: "", title: "", text: "", options: [] })
    property int selected: -1
    property string note: ""
    property bool dressed: false
    property bool overWorld: false
    readonly property bool plating: dressed ? Shape.plated : overWorld
    readonly property rect box: Qt.rect(x, y, width, height)
    readonly property real reached: plating ? Shape.reach(Shape.phase, Shape.kind, Shape.progress, box) : 0
    readonly property bool shaped: reached > 0
    readonly property var look: shaped ? Shape.blend(dressed ? "panel" : "neutral", reached)
                                       : ({ plate: Shape.plate, opacity: 1, material: "solid", amount: 0, game: false })
    readonly property bool gameText: dressed && shaped && look.game
    readonly property color focusColour: !dressed ? Theme.accent : shaped ? (look.game ? Shape.focus : Theme.accent)
                                         : !Shape.plated ? Shape.focus : Theme.accent
    // What MUN draws in ash, patina and moon on its panel; on a plate, the
    // set's text colour (the game's once its plan says so, MUN's moon
    // before): every text on a plate is the proven one.
    readonly property color plateText: gameText ? Shape.text : Theme.moon
    readonly property color textColour: shaped ? plateText : Theme.moon
    readonly property color secondColour: shaped ? plateText : Theme.ash
    readonly property color labelColour: shaped ? plateText : Theme.patina
    // A pointer chose option `index`.
    signal optionClicked(int index)

    readonly property var options: content.options || []
    readonly property int windowHeight: 560

    // Room for the shadow, flat and projected.
    margin: 170
    projection: Theme.perspectiveY(-16, 1600, margin + width, margin + height / 2)
    x: 1150
    width: 640
    height: column.height + 88
    // On whole pixels, so its copy is drawn as it is.
    y: Math.round(Math.max(150, Math.min(Theme.orb.y - height / 2, Theme.canvasHeight - 130 - height)))
    Behavior on y {
        enabled: root.settled
        NumberAnimation { duration: 450; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.settle }
    }
    // The first layout places the panel; only later changes slide it.
    property bool settled: false
    Component.onCompleted: Qt.callLater(() => root.settled = true)

    onContentChanged: refresh()
    onSelectedChanged: refresh()
    onNoteChanged: refresh()
    onShapedChanged: refresh()
    onFocusColourChanged: refresh()
    onReachedChanged: refresh()
    Connections {
        target: Shape
        enabled: root.dressed
        function onChanged() { root.refresh() }
    }

    // Pointer input lands on what the view shows: back through the
    // projection to the flat option under it.
    MouseArea {
        parent: root
        x: -root.margin
        y: -root.margin
        width: root.width + 2 * root.margin
        height: root.height + 2 * root.margin
        onClicked: (mouse) => {
            const point = root.toContent(Qt.point(mouse.x - root.margin, mouse.y - root.margin))
            const inWindow = panel.mapToItem(window, point.x, point.y)
            if (inWindow.x < 0 || inWindow.x > window.width || inWindow.y < 0 || inWindow.y > window.height)
                return
            const at = panel.mapToItem(list, point.x, point.y)
            for (let i = 0; i < rows.count; ++i) {
                const entry = rows.itemAt(i)
                if (entry && !entry.head && at.y >= entry.y && at.y < entry.y + entry.height) {
                    root.optionClicked(i)
                    return
                }
            }
        }
    }

    Item {
        id: panel
        anchors.fill: parent

        Box {
            anchors.fill: parent
            visible: !root.shaped
            radii: [26]
            gradient: ({ type: "linear", angle: 160, stops: [[0, Theme.rgba(62, 92, 130, 0.16)], [0.6, Theme.rgba(20, 24, 32, 0.35)], [1, Theme.rgba(7, 8, 10, 0.4)]] })
            insets: [{ y: 1, color: Theme.rgba(255, 255, 255, 0.08) }, { spread: 1, color: Theme.rgba(143, 176, 214, 0.10) }]
        }
        // Cast by the panel, whichever surface it shows (placed around its parent).
        Shadow {
            z: -1
            radii: [26]
            shadows: [{ y: 40, blur: 80, spread: -30, color: Theme.rgba(0, 0, 0, 0.8) }]
        }
        Material {
            anchors.fill: parent
            visible: root.shaped
            radii: [26]
            plate: root.look.plate
            material: root.look.material
            opacity: root.look.opacity
            amount: root.look.amount
        }

        Column {
            id: column
            x: 48
            y: 44
            width: root.width - 96

            MarkText {
                text: root.content.kicker
                size: 15
                tracking: 0.24
                color: root.labelColour
            }
            Item { width: 1; height: 14 }
            UiText {
                width: parent.width
                text: root.content.title
                size: 56
                weight: 700
                stretch: 118
                tracking: -0.02
                cssLineHeight: 1.04
                wrapMode: Text.WordWrap
                color: root.textColour
            }
            Item { width: 1; height: 18 }
            UiText {
                // 32ch of Archivo at 25 px.
                width: Math.min(parent.width, 458)
                text: root.content.text
                size: 25
                cssLineHeight: 1.45
                color: root.secondColour
                wrapMode: Text.WordWrap
            }
            Item { width: 1; height: 30 }

            Item {
                id: window
                width: parent.width
                height: Math.min(root.windowHeight, list.height)
                clip: true
                readonly property bool overflows: list.height > root.windowHeight
                // Keep the chosen option's bottom 40 px above the window's.
                readonly property real scroll: {
                    const row = root.selected >= 0 && root.selected < rows.count ? rows.itemAt(root.selected) : null
                    return row ? Math.max(0, row.y + row.height - (root.windowHeight - 40)) : 0
                }

                Column {
                    id: list
                    y: -window.scroll
                    width: parent.width
                    spacing: 8
                    Repeater {
                        id: rows
                        model: root.options.length
                        Item {
                            id: entry
                            required property int index
                            readonly property var option: root.options[index] || ({})
                            readonly property bool head: option.kind === "head"
                            width: list.width
                            height: head ? (index === 0 ? 0 : 18) + heading.height + 4 : row.implicitHeight

                            // The window clips; its bottom fade is approximated
                            // per option (the software renderer has no masks).
                            // Only opacity: a Column leaves invisible items out
                            // of its layout, which would move the others.
                            readonly property real windowTop: y - window.scroll
                            readonly property real middle: windowTop + height / 2
                            opacity: !window.overflows || middle <= root.windowHeight * 0.88 ? 1
                                     : Math.max(0, (root.windowHeight - middle) / (root.windowHeight * 0.12))

                            MarkText {
                                id: heading
                                visible: entry.head
                                y: entry.index === 0 ? 0 : 18
                                text: entry.head ? entry.option.label : ""
                                size: 13
                                tracking: 0.26
                                color: root.labelColour
                            }
                            OptionRow {
                                id: row
                                visible: !entry.head
                                width: parent.width
                                height: parent.height
                                label: entry.head ? "" : entry.option.label
                                value: entry.option.value || ""
                                choice: entry.option.kind === "choice"
                                on: entry.index === root.selected
                                shaped: root.shaped
                                focusColour: root.focusColour
                                textColour: root.plateText
                            }
                        }
                    }
                }
            }

            Item { width: 1; height: 18 }
            UiText {
                width: parent.width
                height: Math.max(30, implicitHeight)
                text: root.note
                size: 21
                color: root.secondColour
                wrapMode: Text.WordWrap
            }
        }
    }
}
