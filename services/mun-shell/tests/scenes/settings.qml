import QtQuick
import MUN.Shell

// A scene of tests/behaviour.py: ShellSettings alone, against the settings
// file the test wrote in the state directory. It prints a SETTINGS line with
// every value and the last error, runs CONFIG's steps one after another
// ({"set": {property: value}}, {"call": "resetKeepingLanguage"},
// {"report": label}) and quits.
Item {
    readonly property var config: CONFIG

    function report(label) {
        console.log("SETTINGS " + JSON.stringify({
            label: label, language: ShellSettings.language, clockFormat: ShellSettings.clockFormat,
            safeArea: ShellSettings.safeArea, systemSounds: ShellSettings.systemSounds,
            gameSounds: ShellSettings.gameSounds, shapeMode: ShellSettings.shapeMode,
            reduceMotion: ShellSettings.reduceMotion, autoPowerOffHours: ShellSettings.autoPowerOffHours,
            activeResolution: ShellSettings.activeResolution, restarting: ShellSettings.restarting,
            lastError: ShellSettings.lastError
        }))
    }

    Component.onCompleted: {
        report("start")
        for (const step of config.steps) {
            if (step.set)
                for (const key in step.set)
                    ShellSettings[key] = step.set[key]
            if (step.call)
                ShellSettings[step.call]()
            if (step.report)
                report(step.report)
        }
        Qt.quit()
    }
}
