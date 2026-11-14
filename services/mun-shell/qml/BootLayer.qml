import QtQuick
import MUN.Shell

// The console turning on, once per boot: MUN's start-up, the console waking
// in the dark (BootIntro draws it, part by part), over the start-up sound
// (sounds/startup.wav, 13.05 s); and off.
//
// One clock, t, in seconds of the sound (BootClock, src/startupclock.h). While
// the sound plays and can be trusted, t is what has been heard of it
// (SystemSounds.heard), read on every frame: the picture follows what is
// heard, even when the device is slow to start or falls behind while the
// system loads. The screen stays black while the sound waits for its device
// (up to 3 s), and the picture holds while the sound stalls (up to 2 s), then
// follows it again. A sound that does not start, does not advance from its
// start, stalls longer, goes back, runs at a wrong speed or whose device
// keeps recovering is no longer followed: t goes on alone, at real speed,
// from where it was, never back and never again from the start. With the
// interface sounds off or no device, t runs alone from the start. Every
// change of clock is one line in the journal.
//
// The system loads meanwhile. Once the card service and the launcher have
// answered (`ready`), "A Skip" shows at the bottom right, and A (or Enter,
// or Space) ends the start-up: the sound fades out in 0.1 s and Home comes
// in. Until then no key does anything. When the picture has faded
// (BootIntro's `faded`), Home comes in if the system is ready, while the
// sound rings on; if it is not, the LED stays, breathing, until it is, or
// kReady after the start whatever it does. powerOff() brings the black back
// while the system shuts down.
//
// Before the start-up begins, the console is in standby: its LED lit, as
// the picture the system shows before the shell runs has it (that picture is
// this layer's first frame, made at build time: deploy/standby-frame.py), so
// nothing changes on screen when the shell takes the display over.
//
// Everything drawn (the parts, the waiting LED, the flash, the tube, the
// hint) exists only while the layer is visible: once it has faded out, the
// loader below lets it all go, images and textures with it, and powerOff()
// brings it back for the black.
Rectangle {
    id: root
    // Shown: the layer covers the screen.
    property bool shown: true
    // Seconds into the start-up sound; below 0, nothing is lit yet.
    property real t: -1
    // The start-up is on screen, its LED waits for the sound device before
    // it, or waits for the system after it.
    readonly property bool playing: running || waiting || warming
    property bool running: false
    property bool waiting: false
    // Before the start-up's sound, the device has to have played its
    // silence for kWarm: a display hears a stream only a moment after it
    // starts (on a board's HDMI display, about two seconds), and the
    // sound's first notes would be lost while the picture moved. main.cpp
    // opens the device as the shell starts, so this is usually over before
    // the interface has loaded; otherwise the LED breathes in the dark.
    // Played well, not just open (SystemSounds.playingFor()). While a device
    // is expected (SystemSounds.expecting()) the start-up waits for one that
    // plays well; after that, without a device it does not wait, and for
    // one that is open but does not play well, up to kWarmLimit.
    readonly property int kWarm: 2000
    readonly property int kWarmLimit: 3000
    property bool warming: false
    property double warmFrom: 0
    // Standby: the console on, before its start-up begins (the layer shown
    // at the first start of a boot, until the sound or the picture starts).
    // The LED is lit from the first frame, as the picture the system shows
    // before the shell runs has it (deploy/mun-standby, made from this
    // frame), held 0.4 s, then breathing; it fades as the start-up begins.
    property bool before: true
    readonly property bool standby: shown && before && !leaving
    property bool leaving: false
    property bool late: false
    readonly property bool ready: CardClient.readerAvailable && LaunchClient.available
    readonly property bool canSkip: playing && ready && !leaving
    signal finished()

    readonly property int kReady: 20000

    BootClock { id: clock }

    function start() {
        late = false
        leaving = false
        waiting = false
        t = -1
        ready_.restart()
        if (!ShellSettings.systemSounds) {
            before = false
            running = true
            clock.begin(false, 0)
        } else if (soundReady()) {
            begin()
        } else {
            warming = true
            warmFrom = Date.now()
            warm.start()
        }
    }
    // The device has played well long enough, or there is none to wait for.
    function soundReady() {
        if (SystemSounds.playingFor() * 1000 >= kWarm)
            return true
        if (SystemSounds.expecting())
            return false
        if (!SystemSounds.deviceOpen())
            return true
        return Date.now() - warmFrom >= kWarmLimit
    }
    function begin() {
        before = false
        running = true
        clock.begin(true, SystemSounds.recoveries())
        SystemSounds.play("startup")
    }
    function tick() {
        t = clock.tick(SystemSounds.heard("startup"), SystemSounds.recoveries())
        if (t >= times.faded && running && !waiting && !leaving) {
            if (ready || late)
                leave()
            else
                waiting = true
        }
        if (waiting && t >= times.end + 1)
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
        before = false
        waiting = false
        warming = false
        running = false
        warm.stop()
        ready_.stop()
        shown = false
        finished()
    }
    function powerOff() {
        running = false
        waiting = false
        warming = false
        leaving = false
        warm.stop()
        ready_.stop()
        t = -1
        shown = true
    }

    onReadyChanged: if (waiting && ready) leave()

    FrameAnimation {
        running: root.running
        onTriggered: root.tick()
    }
    // The sound device before the start-up (kWarm): checked until it has
    // played long enough, or until none is expected any more.
    Timer {
        id: warm
        interval: 50
        repeat: true
        onTriggered: {
            if (!root.soundReady())
                return
            warm.stop()
            const playing = SystemSounds.playingFor()
            console.info("mun-shell: start-up: waited " + ((Date.now() - root.warmFrom) / 1000).toFixed(2)
                         + " s for the sound device: "
                         + (playing >= 0 ? "it had played well for " + playing.toFixed(2) + " s"
                            : SystemSounds.deviceOpen() ? "it was open but did not play well" : "none came"))
            root.warming = false
            root.begin()
        }
    }
    Connections {
        target: SystemSounds
        // The sound started after the picture had gone on alone (a slow
        // device): followed again while the screen is still black.
        function onStarted(name) {
            if (name === "startup" && root.running)
                clock.started()
        }
        // No device: the picture does not wait for a sound that will not come.
        function onDropped(name) {
            if (name === "startup" && root.running)
                clock.dropped()
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

    // The start-up's times, from a part that never paints.
    BootIntro { id: times; visible: false }

    Loader {
        anchors.fill: parent
        active: root.visible
        sourceComponent: Component {
            Item {
                // The screen: its gradient and the picture on it shake
                // together on the hit; the picture fades at the end. Parts
                // paint over their own areas, in canvas coordinates.
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
                    // The LED in standby (before the start-up: lit at once,
                    // held, then breathing) or waiting for the system after
                    // it (coming in from dark), breathing.
                    BootIntro {
                        id: waitingLed
                        objectName: "standbyLed"
                        part: BootIntro.Led
                        opacity: root.waiting || root.standby ? 1 : 0
                        visible: opacity > 0
                        Behavior on opacity { NumberAnimation { duration: 600 } }
                        breath: 1
                        x: area.x; y: area.y; width: area.width; height: area.height
                        SequentialAnimation on breath {
                            running: root.standby
                            PauseAnimation { duration: 400 }
                            SequentialAnimation {
                                loops: Animation.Infinite
                                NumberAnimation { from: 1; to: 0; duration: 1200; easing.type: Easing.InOutSine }
                                NumberAnimation { from: 0; to: 1; duration: 1200; easing.type: Easing.InOutSine }
                            }
                        }
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
                // The tube's vignette over everything.
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
        }
    }
}
