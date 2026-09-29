import QtQuick
import MUN.Shell

// Root of the shell: Home as the design draws it, on a 1920x1080 canvas.
// Beneath, the network of light (Backdrop) and the large object of the entry
// in focus (HeroIcon); over them, inside the safe area, the status line, the
// menu arcs leaning into the scene, the panel on the right, the path and the
// key hints; over everything, the game hand-over, dialogs and the start-up.
//
// Navigation has the design's three levels: 0 the main arc (Game Card, My
// games, Settings, Turn off), 1 the Settings arc, 2 the options of the panel
// in view. A on Turn off asks to confirm and turns the console off.
// Up and Down move within a level, wrapping; Right or A (Enter, Space) goes
// in or changes a choice; Left or B (Escape, Backspace) goes back or changes
// a choice the other way. Back from the Settings arc lands on Settings.
Window {
    id: window
    visible: true
    visibility: Window.FullScreen
    color: Theme.screen
    title: "MUN Shell"

    // The start-up plays once per boot (the binary tells: shellFirstStart);
    // the launcher restarts the shell around every game, and coming back
    // from one lands on Home with the session's result.
    readonly property bool firstStart: typeof shellFirstStart === "undefined" ? true : shellFirstStart
    // Input is taken once the start-up has shown, and not while the console
    // turns off or the shell restarts for a new resolution.
    property bool powered: !firstStart
    readonly property bool interactive: powered && !ShellSettings.restarting

    // ------------------------------------------------------------ the card

    // offline (no reader), absent, reading, valid, invalid, released.
    readonly property string cardMode: !CardClient.readerAvailable ? "offline"
        : ["absent", "reading", "valid", "invalid", "released"].indexOf(CardClient.state) >= 0 ? CardClient.state : "reading"
    readonly property bool cardReady: cardMode === "valid"
    readonly property bool hasGame: cardReady && CardClient.info.kind === "game"
    readonly property bool launchable: hasGame && !!CardClient.info.entry && LaunchClient.available && LaunchClient.state === "idle"
    readonly property string launchBlocker: LaunchClient.available ? I18n.t("Busy", "Ocupada") : I18n.t("Unavailable", "No disponible")
    // Asked to play and not refused yet: the launcher is taking the screen.
    readonly property bool starting: LaunchClient.requestPending || (LaunchClient.available && LaunchClient.state !== "idle")

    readonly property string cardDetail: {
        switch (cardMode) {
        case "valid": return CardClient.info.title || "Game Card"
        case "invalid": return I18n.t("Not valid", "No válida")
        case "reading": return I18n.t("Reading…", "Leyendo…")
        case "released": return I18n.t("Remove it", "Retírala")
        case "offline": return I18n.t("No reader", "Sin lector")
        }
        return I18n.t("Empty", "Vacía")
    }
    readonly property string slotText: {
        switch (cardMode) {
        case "valid": return CardClient.info.title || "Game Card"
        case "invalid": return I18n.t("Card not valid", "Tarjeta no válida")
        case "reading": return I18n.t("Reading the card…", "Leyendo la tarjeta…")
        case "released": return I18n.t("Remove the card", "Retira la tarjeta")
        case "offline": return I18n.t("Reader unavailable", "Lector no disponible")
        }
        return I18n.t("Slot empty", "Ranura vacía")
    }
    readonly property bool online: SystemInfo.wiredConnected || SystemInfo.wifiConnected
    readonly property string networkText: SystemInfo.wiredConnected ? I18n.t("Ethernet", "Cable")
        : SystemInfo.wifiConnected ? "Wi-Fi" : I18n.t("Offline", "Sin conexión")

    // ------------------------------------------------------------ navigation

    property int level: 0
    property int previousLevel: 0
    property int mainIndex: 0
    property int settingsIndex: 0
    property int optionIndex: 0
    property var modal: null
    property int modalIndex: 0
    // A line of feedback under the options (a refused launch, a failed save);
    // cleared when the focus moves.
    property string feedback: ""

    readonly property var mainEntries: [
        { key: "card", label: "Game Card", detail: cardDetail },
        { key: "games", label: I18n.t("My games", "Mis juegos") },
        { key: "settings", label: I18n.t("Settings", "Configuración") },
        { key: "power", label: I18n.t("Turn off", "Apagar") }
    ]
    readonly property var settingsEntries: [
        { key: "personal", label: I18n.t("Account and language", "Cuenta e idioma") },
        { key: "av", label: I18n.t("Picture and sound", "Imagen y sonido") },
        { key: "network", label: I18n.t("Network", "Red") },
        { key: "system", label: I18n.t("System", "Sistema") }
    ]
    readonly property bool inSettings: level === 1 || (level === 2 && previousLevel === 1)
    readonly property var focusedEntry: inSettings ? settingsEntries[settingsIndex] : mainEntries[mainIndex]
    readonly property var panel: panels.build(focusedEntry.key)
    readonly property var options: panel.options
    readonly property var selectedOption: level === 2 ? options[optionIndex] : null
    readonly property string note: feedback !== "" ? feedback : selectedOption && selectedOption.note ? selectedOption.note : ""

    Panels { id: panels; shell: window }

    function firstSelectable(from, step) {
        const n = options.length
        for (let k = 0; k < n; ++k) {
            const j = ((from + step * k) % n + n) % n
            if (options[j].kind !== "head")
                return j
        }
        return 0
    }

    // The menus' sounds, by what the player did: "move" (the focus or a
    // choice changed), "enter" (in, or an action run), "back". Only a key or
    // a pointer that changed something sounds; the console's own changes
    // (a card arriving, a result shown) do not.
    function sound(name) {
        if (ShellSettings.systemSounds)
            SystemSounds.play(name)
    }

    function move(step) {
        if (modal) {
            const count = modal.actions.length
            modalIndex = (modalIndex + step + count) % count
            if (count > 1)
                sound("move")
            return
        }
        feedback = ""
        const before = [level, mainIndex, settingsIndex, optionIndex].join()
        if (level === 0)
            mainIndex = (mainIndex + step + mainEntries.length) % mainEntries.length
        else if (level === 1)
            settingsIndex = (settingsIndex + step + settingsEntries.length) % settingsEntries.length
        else
            optionIndex = firstSelectable(optionIndex + step, step)
        if ([level, mainIndex, settingsIndex, optionIndex].join() !== before)
            sound("move")
    }

    function side(step) {
        if (modal) {
            move(step)
            return
        }
        if (level !== 2) {
            step > 0 ? enter() : back()
            return
        }
        const option = options[optionIndex]
        if (!option || option.kind !== "choice") {
            if (step < 0)
                back()
            return
        }
        change(option, step)
    }

    function change(option, step) {
        const n = option.values.length
        option.choose((option.index + step + n) % n)
        sound("move")
    }

    function enter() {
        if (modal) {
            sound("enter")
            modal.actions[modalIndex].run()
            return
        }
        feedback = ""
        if (level === 0 && focusedEntry.key === "power") {
            sound("enter")
            askPowerOff()
        } else if (level === 0 && focusedEntry.key === "settings") {
            sound("enter")
            level = 1
            settingsIndex = 0
        } else if (level < 2 && options.length > 0) {
            sound("enter")
            previousLevel = level
            level = 2
            optionIndex = firstSelectable(0, 1)
        } else if (level === 2) {
            const option = options[optionIndex]
            if (option && option.kind === "choice") {
                change(option, 1)
            } else if (option && option.run) {
                sound("enter")
                option.run()
            }
        }
    }

    // Pointer input, as the design has it: a click on an entry focuses it,
    // or enters it when it has the focus; a click on an option chooses and
    // runs it; the wheel moves.
    function clickEntry(arc, index) {
        if (!interactive || starting || modal)
            return
        feedback = ""
        if (arc === 0) {
            level = 0
            if (mainIndex === index) enter(); else { mainIndex = index; sound("move") }
        } else {
            level = 1
            if (settingsIndex === index) enter(); else { settingsIndex = index; sound("move") }
        }
    }
    function clickOption(index) {
        if (!interactive || starting || modal)
            return
        if (level !== 2) {
            previousLevel = level
            level = 2
        }
        optionIndex = index
        enter()
    }

    function back() {
        if (modal) {
            sound("back")
            const dismiss = modal.dismiss || closeModal
            dismiss()
            return
        }
        feedback = ""
        if (level === 2) {
            sound("back")
            level = previousLevel
        } else if (level === 1) {
            sound("back")
            level = 0
            mainIndex = 2
        }
    }

    // A panel that loses its options (the card left) takes the focus back
    // to its arc; one whose options changed keeps it on an option.
    onPanelChanged: {
        if (level !== 2)
            return
        if (options.length === 0)
            level = previousLevel
        else if (optionIndex >= options.length || options[optionIndex].kind === "head")
            optionIndex = firstSelectable(Math.min(optionIndex, options.length - 1), 1)
    }
    // The card's options are for the card that was there.
    onCardModeChanged: {
        wake()
        if (level === 2 && previousLevel === 0)
            level = 0
    }

    // ------------------------------------------------------------ actions

    function openModal(content) {
        modalIndex = content.initial || 0
        modal = content
    }
    function closeModal() {
        modal = null
        Qt.callLater(showResult)
    }
    // A session's result, as a key: the same result shown twice has the same
    // key; a warning that arrives after the session ended (a cleanup that
    // failed) changes it.
    function resultKey(result) {
        const copy = Object.assign({}, result)
        delete copy.acknowledged
        return JSON.stringify(copy)
    }
    function showResult() {
        if (!LaunchClient.hasUnacknowledgedResult || !powered)
            return
        const result = LaunchClient.lastResult
        const key = resultKey(result)
        if (!modal)
            openModal(panels.sessionResult(result, key))
        else if (modal.resultKey !== undefined && modal.resultKey !== key)
            openModal(panels.sessionResult(result, key))   // the result on screen changed: show the new one
        // Any other dialog: the result waits until it closes (closeModal).
    }
    function dismissResult() {
        // Only what the player has seen is acknowledged; a newer result is
        // shown instead of being dismissed with the old one.
        if (modal && modal.resultKey === resultKey(LaunchClient.lastResult))
            LaunchClient.acknowledge()
        closeModal()
    }
    function play() {
        if (!launchable)
            return
        feedback = ""
        LaunchClient.launch(CardClient.card.slot, CardClient.card.serial, CardClient.info.version)
    }
    function eject() {
        if (!cardReady || !LaunchClient.available || LaunchClient.state !== "idle" || LaunchClient.releasePending)
            return
        feedback = ""
        LaunchClient.release(CardClient.card.serial)
    }
    // A resolution is chosen in a dialog; the shell restarts in it and asks,
    // for trialLength seconds, whether to keep it (ShellSettings).
    readonly property int trialLength: 15
    property int trialSeconds: trialLength
    function askResolution() { openModal(panels.resolution()) }
    function changeResolution(mode) {
        modal = null
        if (mode === ShellSettings.activeResolution && !ShellSettings.resolutionOnTrial)
            return
        ShellSettings.changeResolution(mode)
    }
    function askKeepResolution() {
        trialSeconds = trialLength
        openModal(panels.resolutionTrial())
    }
    function keepResolution() {
        ShellSettings.keepResolution()
        closeModal()
    }
    function revertResolution() {
        modal = null
        ShellSettings.revertResolution()
    }
    Timer {
        interval: 1000
        repeat: true
        running: !!window.modal && window.modal.trial === true && !ShellSettings.restarting
        onTriggered: {
            window.trialSeconds -= 1
            if (window.trialSeconds <= 0)
                window.revertResolution()
        }
    }
    // A start that follows a change of resolution or a reset returns to where
    // the player was.
    // The way a player gets there, one level at a time, so each panel is
    // built for its entry before its options take the focus.
    function resumeSettings(where) {
        const key = where === "reset" ? "system" : "av"
        mainIndex = 2
        settingsIndex = Math.max(0, settingsEntries.findIndex(e => e.key === key))
        level = 1
        previousLevel = 1
        level = 2
        optionIndex = where === "reset" ? options.length - 1 : firstSelectable(0, 1)
    }
    // After the start-up, or at once: the resolution on trial asks first,
    // then a session's result.
    function afterStart() {
        if (ShellSettings.resolutionOnTrial)
            askKeepResolution()
        else
            showResult()
    }

    function showCardError() { openModal(panels.cardError()) }
    function showAbout() { openModal(panels.about()) }
    function askReset() { openModal(panels.reset()) }
    function askPowerOff() { openModal(panels.powerOff()) }
    function powerOff() {
        modal = null
        powered = false
        boot.powerOff()
        PowerControl.powerOff()
    }

    Connections {
        target: LaunchClient
        function onLaunchRejected(code, message) { window.feedback = I18n.launchError({ code: code, message: message }) }
        function onReleaseFinished(ok, code, message) { if (!ok) window.feedback = I18n.launchError({ code: code, message: message }) }
        function onHasUnacknowledgedResultChanged() { window.showResult() }
    }
    Connections {
        target: PowerControl
        // On success the system goes down; a failure gives the screen back.
        function onLastErrorChanged() {
            if (PowerControl.lastError === "" || PowerControl.busy)
                return
            boot.shown = false
            window.powered = true
            window.openModal(panels.powerOffFailed(PowerControl.lastError))
        }
    }
    Connections {
        target: ShellSettings
        function onLastErrorChanged() {
            if (ShellSettings.lastError !== "")
                window.feedback = I18n.t("This choice could not be saved; it lasts until the console restarts.",
                                         "No se pudo guardar esta elección; dura hasta que la consola se reinicie.")
        }
    }

    // ------------------------------------------------------------ time

    // Rest: three minutes after the last input the scene slows to a stop
    // (the network's drift, the object's float, the gear), so a console left
    // alone draws nothing; any key brings the motion back.
    property bool resting: false
    Timer { id: restTimer; interval: 3 * 60 * 1000; running: true; onTriggered: window.resting = true }
    // The automatic power off counts time without input on these menus;
    // while a game runs the shell is stopped, so it never counts.
    Timer {
        id: autoOff
        interval: Math.max(1, ShellSettings.autoPowerOffHours) * 3600 * 1000
        running: ShellSettings.autoPowerOffHours > 0 && window.powered && !window.starting
        onTriggered: window.powerOff()
    }
    function wake() {
        resting = false
        restTimer.restart()
        if (autoOff.running)
            autoOff.restart()
    }
    Timer {
        interval: 10000
        running: true
        repeat: true
        onTriggered: SystemInfo.refreshNetwork()
    }

    Component.onCompleted: {
        if (ShellSettings.resume !== "")
            resumeSettings(ShellSettings.resume)
        if (firstStart)
            bootDelay.start()
        else
            afterStart()
    }
    Timer { id: bootDelay; interval: 250; onTriggered: boot.start() }

    // ------------------------------------------------------------ the scene

    Item {
        id: canvas
        width: Theme.canvasWidth
        height: Theme.canvasHeight
        anchors.centerIn: parent
        focus: true

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.NoButton
            property real travel: 0
            onWheel: (wheel) => {
                window.wake()
                travel += wheel.angleDelta.y
                if (Math.abs(travel) >= 120 && window.interactive && !window.starting) {
                    window.move(travel > 0 ? -1 : 1)
                    travel = 0
                }
            }
        }

        Keys.onPressed: (event) => {
            event.accepted = true
            window.wake()
            if (!window.interactive || window.starting)
                return
            switch (event.key) {
            case Qt.Key_Up: window.move(-1); break
            case Qt.Key_Down: window.move(1); break
            case Qt.Key_Left: window.side(-1); break
            case Qt.Key_Right: window.side(1); break
            case Qt.Key_Return: case Qt.Key_Enter: case Qt.Key_Space: case Qt.Key_A: window.enter(); break
            case Qt.Key_Escape: case Qt.Key_Backspace: case Qt.Key_Back: case Qt.Key_B: window.back(); break
            default: event.accepted = false
            }
        }

        Backdrop {
            id: backdrop
            anchors.fill: parent
            orb: Theme.orb
            hour: status.now.getHours() + status.now.getMinutes() / 60
            glowColor: Theme.cardLight
            running: !window.resting && window.powered
        }
        HeroIcon {
            id: hero
            width: 640
            height: 640
            x: Theme.orb.x - width / 2
            y: Theme.orb.y - height / 2 + bob
            icon: window.focusedEntry.key
            lit: window.cardReady ? 1 : 0.16
            running: !window.resting && window.powered
            onIconChanged: backdrop.ripple()
        }

        Item {
            id: safe
            anchors.fill: parent
            scale: ShellSettings.safeArea / 100
            Behavior on scale { NumberAnimation { duration: 500; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.settle } }

            StatusBar {
                id: status
                anchors.right: parent.right
                anchors.rightMargin: Theme.gutter
                y: 64
                slotText: window.slotText
                cardReady: window.cardReady
                networkText: window.networkText
                online: window.online
                clockFormat: ShellSettings.clockFormat
            }

            Item {
                anchors.fill: parent
                ArcMenu {
                    anchors.fill: parent
                    entries: window.mainEntries
                    current: window.mainIndex
                    radius: 340
                    spread: 24
                    hidden: window.inSettings
                    dimmed: window.level === 2 && window.previousLevel === 0
                    onActivated: (index) => { window.wake(); window.clickEntry(0, index) }
                }
                ArcMenu {
                    anchors.fill: parent
                    entries: window.settingsEntries
                    current: window.settingsIndex
                    radius: 350
                    spread: 17
                    compact: true
                    hidden: !window.inSettings
                    dimmed: window.level === 2 && window.previousLevel === 1
                    onActivated: (index) => { window.wake(); window.clickEntry(1, index) }
                }
            }

            DetailPanel {
                content: window.panel
                selected: window.level === 2 ? window.optionIndex : -1
                note: window.note
                onOptionClicked: (index) => { window.wake(); window.clickOption(index) }
            }

            MarkText {
                x: Theme.gutter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 66
                size: 15
                tracking: 0.24
                readonly property string home: I18n.t("HOME", "INICIO")
                readonly property string separator: "  ›  "
                text: window.level === 0 ? home
                    : window.inSettings ? home + separator + I18n.t("SETTINGS", "CONFIGURACIÓN")
                                          + (window.level === 2 ? separator + window.focusedEntry.label.toUpperCase() : "")
                    : home + separator + window.focusedEntry.label.toUpperCase()
            }

            Row {
                anchors.right: parent.right
                anchors.rightMargin: Theme.gutter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 60
                spacing: 40
                Repeater {
                    model: [["A", I18n.t("Select", "Seleccionar")], ["B", I18n.t("Back", "Atrás")]]
                    Row {
                        required property var modelData
                        spacing: 12
                        Rectangle {
                            width: 36
                            height: 36
                            radius: 18
                            color: "transparent"
                            border.width: 2
                            border.color: Theme.ash
                            UiText {
                                anchors.centerIn: parent
                                text: modelData[0]
                                size: 17
                                weight: 700
                            }
                        }
                        UiText {
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData[1]
                            size: 23
                            color: Theme.ash
                        }
                    }
                }
            }
        }

        // The game takes the screen: the launcher stops the shell in a moment.
        Rectangle {
            anchors.fill: parent
            color: Theme.layer
            opacity: window.starting ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 500; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }
            UiText {
                anchors.centerIn: parent
                horizontalAlignment: Text.AlignHCenter
                size: 30
                cssLineHeight: 1.5
                color: Theme.dim
                text: I18n.t("Starting ", "Iniciando ") + (CardClient.info.title || I18n.t("the game", "el juego")) + "…\n"
                      + (LaunchClient.state === "preparing" ? I18n.t("Preparing the game from the Game Card.", "Preparando el juego desde la Game Card.")
                                                            : I18n.t("The game takes over the screen.", "El juego toma la pantalla."))
            }
        }

        ModalLayer {
            content: window.modal
            selected: window.modalIndex
            onChosen: (index) => {
                window.wake()
                if (window.modal && window.modal.actions[index]) {
                    window.modalIndex = index
                    window.modal.actions[index].run()
                }
            }
        }

        BootLayer {
            id: boot
            shown: window.firstStart
            onFinished: {
                window.powered = true
                window.afterStart()
            }
        }

        // The shell restarts in a new resolution: the scene fades out first.
        Rectangle {
            anchors.fill: parent
            color: Theme.screen
            opacity: ShellSettings.restarting ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 500; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }
        }
    }
}
