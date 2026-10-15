import QtQuick
import MUN.Shell

// The console turning on, once per boot: MUN's start-up, the console waking
// in the dark (BootIntro draws it, part by part), over the start-up sound
// (sounds/startup.wav, 13.05 s); and off.
//
// One clock, t, in seconds of the sound. While the sound plays, t is what
// has been heard of it (SystemSounds.heard), read on every frame: the
// picture follows what is heard, even when the device is slow to start or
// falls behind while the system loads. The screen stays black while the
// sound waits for its device (up to kStartWait), and the picture holds while
// the sound stalls (up to kStallWait), then follows it again. With the
// interface sounds off, the sound dropped (no device), or a wait past those
// bounds, t runs on its own from where it was. Every change of clock is one
// line in the journal.
//
// The system loads meanwhile. Once the card service and the launcher have
// answered (`ready`), "A Skip" shows at the bottom right, and A (or Enter,
// or Space) ends the start-up: the sound fades out in 0.1 s and Home comes
// in. Until then no key does anything. When the picture has faded
// (BootIntro's `faded`), Home comes in if the system is ready, while the
// sound rings on; if it is not, the LED stays, breathing, until it is, or
// kReady after the start whatever it does. powerOff() brings the black back
// while the system shuts down.
Rectangle {
    id: root
    // Shown: the layer covers the screen.
    property bool shown: true
    // Seconds into the start-up sound; below 0, nothing is lit yet.
    property real t: -1
    // The start-up is on screen, or its LED waits for the system.
    readonly property bool playing: running || waiting
    property bool running: false
    property bool waiting: false
    property bool leaving: false
    property bool late: false
    readonly property bool ready: CardClient.readerAvailable && LaunchClient.available
    readonly property bool canSkip: playing && ready && !leaving
    signal finished()

    readonly property int kReady: 20000
    // How long the black screen waits for the sound's first samples, and
    // how long the picture holds while the sound stalls.
    readonly property int kStartWait: 3000
    readonly property int kStallWait: 2000

    // The clock: following the sound, or on its own from `ownFrom` at
    // `ownStart` (ms since the epoch).
    property bool audible: false
    property bool heardOnce: false
    property real ownFrom: 0
    property double ownStart: 0
    property real lastHeard: -1
    property double lastHeardAt: 0

    function start() {
        late = false
        leaving = false
        waiting = false
        heardOnce = false
        t = -1
        running = true
        ready_.restart()
        if (ShellSettings.systemSounds) {
            audible = true
            SystemSounds.play("startup")
            noSound.restart()
        } else {
            runAlone(0, "the interface sounds are off")
        }
    }
    function runAlone(from, why) {
        console.info("mun-shell: start-up: the picture runs on its own from " + from.toFixed(2) + " s: " + why)
        audible = false
        noSound.stop()
        ownFrom = from
        ownStart = Date.now()
    }
    function tick() {
        const now = Date.now()
        if (audible) {
            const heard = SystemSounds.heard("startup")
            if (isNaN(heard)) {
                // Not started yet (noSound waits for it), or over: played
                // out or dropped (onDropped says which, first).
                if (heardOnce)
                    runAlone(t, "the sound is over (played out or dropped)")
                return
            }
            if (!heardOnce) {
                heardOnce = true
                lastHeard = heard
                lastHeardAt = now
                noSound.stop()
                console.info("mun-shell: start-up: the picture follows the sound")
            }
            if (heard !== lastHeard) {
                if (now - lastHeardAt > 500)
                    console.info("mun-shell: start-up: the sound plays again after " + (now - lastHeardAt) + " ms; the picture held")
                lastHeard = heard
                lastHeardAt = now
            } else if (now - lastHeardAt > kStallWait && heard > 0) {
                // Stalled past the wait: the picture goes on alone from here.
                runAlone(heard, "the sound stalled for " + kStallWait + " ms")
                return
            }
            // While the sound stalls, so does the picture: t stays where
            // the sound is.
            t = heard
        } else {
            t = ownFrom + (now - ownStart) / 1000
        }
        if (t >= background.faded && running && !waiting && !leaving) {
            if (ready || late)
                leave()
            else
                waiting = true
        }
        if (waiting && t >= background.end + 1)
            running = false   // the sound is over: only the LED's breath moves
    }
    function skip() {
        if (!canSkip)
            return
        SystemSounds.stop("startup")
        leave()
    }
    function leave() {
        leaving = true
        waiting = false
        running = false
        noSound.stop()
        ready_.stop()
        shown = false
        finished()
    }
    function powerOff() {
        running = false
        waiting = false
        leaving = false
        noSound.stop()
        ready_.stop()
        t = -1
        shown = true
    }

    onReadyChanged: if (waiting && ready) leave()

    FrameAnimation {
        running: root.running
        onTriggered: root.tick()
    }
    // No first samples after kStartWait: the picture goes on alone.
    Timer {
        id: noSound
        interval: root.kStartWait
        onTriggered: root.runAlone(Math.max(0, root.t), "no sound after " + root.kStartWait + " ms")
    }
    Connections {
        target: SystemSounds
        // The sound started after the picture had (a slow device): follow it
        // again while the screen is still black (the LED lights at 0.3 s).
        function onStarted(name) {
            if (name === "startup" && root.running && !root.audible && root.t < 0.25) {
                root.audible = true
                console.info("mun-shell: start-up: the sound started late; the picture follows it again")
            }
        }
        // No device: the picture does not wait for a sound that will not come.
        function onDropped(name) {
            if (name === "startup" && root.running && root.audible)
                root.runAlone(Math.max(0, root.t), "the sound was dropped (no device)")
        }
    }
    Timer {
        id: ready_
        interval: root.kReady
        onTriggered: {
            root.late = true
            if (root.waiting)
                root.leave()
        }
    }

    anchors.fill: parent
    color: "#000000"
    opacity: shown ? 1 : 0
    visible: opacity > 0
    Behavior on opacity { NumberAnimation { duration: 500; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }

    // The screen: its gradient and the picture on it shake together on the
    // hit; the picture fades at the end. Parts paint over their own areas,
    // in canvas coordinates.
    Item {
        id: screen
        width: Theme.canvasWidth
        height: Theme.canvasHeight
        transform: Translate { x: background.shake.x; y: background.shake.y }

        BootIntro {
            id: background
            part: BootIntro.Screen
            t: root.t
            reduceMotion: ShellSettings.reduceMotion
            x: area.x; y: area.y; width: area.width; height: area.height
        }
        Item {
            anchors.fill: parent
            opacity: background.fade
            visible: opacity > 0 && root.t >= 0
            Repeater {
                model: [BootIntro.Silhouette, BootIntro.Engraving, BootIntro.Mark, BootIntro.Led]
                BootIntro {
                    required property int modelData
                    part: modelData
                    t: root.t
                    reduceMotion: ShellSettings.reduceMotion
                    x: area.x; y: area.y; width: area.width; height: area.height
                }
            }
        }
        // The LED waiting for the system, breathing.
        BootIntro {
            id: waitingLed
            part: BootIntro.Led
            opacity: root.waiting ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 600 } }
            breath: -1
            x: area.x; y: area.y; width: area.width; height: area.height
            SequentialAnimation on breath {
                running: root.waiting
                loops: Animation.Infinite
                NumberAnimation { from: 0; to: 1; duration: 1200; easing.type: Easing.InOutSine }
                NumberAnimation { from: 1; to: 0; duration: 1200; easing.type: Easing.InOutSine }
            }
        }
    }
    // The flash of the two hits.
    Rectangle {
        anchors.fill: screen
        color: "#FFFFFF"
        opacity: background.flash
        visible: opacity > 0.002
    }
    // The tube's lines and vignette over everything.
    BootIntro {
        part: BootIntro.Tube
        x: area.x; y: area.y; width: area.width; height: area.height
    }

    // A Skip, once the system is ready: as Home's key hints are.
    Row {
        objectName: "skipHint"
        anchors.right: screen.right
        anchors.rightMargin: Theme.gutter
        anchors.bottom: screen.bottom
        anchors.bottomMargin: 60
        spacing: 12
        opacity: root.canSkip ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: 400; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.ease } }
        Rectangle {
            width: 36
            height: 36
            radius: 18
            color: "transparent"
            border.width: 2
            border.color: Theme.ash
            UiText {
                anchors.centerIn: parent
                text: "A"
                size: 17
                weight: 700
                color: Theme.moon
            }
        }
        UiText {
            anchors.verticalCenter: parent.verticalCenter
            text: I18n.t("Skip", "Saltar")
            size: 23
            color: Theme.ash
        }
    }
}
