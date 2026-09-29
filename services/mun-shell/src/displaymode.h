// The display mode the shell asks for. Qt opens the display once, when the
// platform starts, so the mode is chosen before QGuiApplication exists and a
// change of resolution restarts the shell (systemd starts it again when it
// exits with kRestartStatus; see deploy/mun-shell.service).
//
// The choice is the player's (Settings › Picture and sound › Resolution),
// kept by ShellSettings: "auto", the display's own preferred mode, or one of
// kModes. A mode is offered only when the display takes it: listed by the
// connector (its EDID on real hardware), or on a virtual connector, which
// takes any mode and gets it as a modeline. A stored mode the display no
// longer offers reads as "auto" for this start and is kept for when it does.
//
// A new mode starts on trial: stored under a one-shot key that prepare()
// removes before Qt opens the display, so if that start fails, shows nothing
// or loses power, the next one uses the previous choice. The shell asks the
// player to keep it and restarts with the previous one if nobody answers.
//
// One display: the first connected connector of /dev/dri/card0, the device
// the unit requires. Games set their own modes once the shell has released
// the display.
#pragma once

#include <QSettings>
#include <QString>
#include <QStringList>

namespace display {

// The exit status that asks systemd to start the shell again.
constexpr int kRestartStatus = 75;

// The modes the shell can offer, 16:9, smallest first.
extern const QStringList kModes;

struct Plan {
    QString connector;    // Qt's name of the output, "HDMI1", "Virtual1"; empty without a display
    QString preferred;    // the display's own mode, "1920x1080"; empty when unknown
    QStringList offered;  // the modes of kModes this display takes
    QString active = QStringLiteral("auto");  // what this start asked for: "auto" or a mode
    bool trial = false;   // `active` is on trial: kept only if the player confirms it
    QString resume;       // where the previous start asked this one to return: "resolution", "reset" or ""
};

// Reads the choice from `settings` (and consumes a trial), reads the display
// from sysfs, and prepares Qt's platform for the mode: QT_QPA_KMS_CONFIG,
// written to the runtime directory, and QT_SCALE_FACTOR, unless the
// environment already sets it. Only for linuxfb or eglfs; any other platform
// (a desktop during development) gets its defaults. Call once, before
// QGuiApplication.
void prepare(QSettings &settings);

// What prepare() decided; the defaults when it has not run.
const Plan &plan();

// Asks the next start to return to `where` (see Plan::resume).
void setResume(const QString &where);

// The settings keys, shared with ShellSettings.
extern const QString kResolutionKey;
extern const QString kTrialKey;

} // namespace display
