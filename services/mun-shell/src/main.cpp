// MUN Shell entry point. Platform selection (framebuffer, software
// renderer, evdev input) is decided by the environment the systemd unit sets,
// so the same binary runs under a desktop compositor for development.
#include <QDir>
#include <QFile>
#include <QFont>
#include <QFontDatabase>
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickWindow>
#include <QSettings>

#include "displaymode.h"
#include "framewatch.h"
#include "gamepads.h"
#include "shellsettings.h"
#include "systemsounds.h"

#include <algorithm>
#include <chrono>

#include <fcntl.h>
#include <unistd.h>

namespace {

// The standby picture the system drew while the console started
// (deploy/mun-standby) stays in the kernel's framebuffer, which the kernel
// shows again whenever no program drives the display, as between the shell
// and a game. Once the shell's own frame is on screen, the framebuffer goes
// back to black, as it was before. The picture is drawn only into a
// framebuffer of its size, 1920x1080 at 32 bits; not where Qt draws into
// the framebuffer itself (linuxfb without DRM).
void clearStandbyPicture()
{
    if (QGuiApplication::platformName() == QLatin1String("linuxfb") && !qEnvironmentVariableIsSet("QT_QPA_FB_DRM"))
        return;
    const int fd = ::open("/dev/fb0", O_WRONLY | O_CLOEXEC);
    if (fd < 0) {
        qWarning("mun-shell: the standby picture stays: cannot open the framebuffer");
        return;
    }
    const std::vector<char> black(size_t(7680) * 1080, 0);
    size_t done = 0;
    while (done < black.size()) {
        const ssize_t n = ::write(fd, black.data() + done, black.size() - done);
        if (n <= 0)
            break;
        done += size_t(n);
    }
    ::close(fd);
    if (done < black.size())
        qWarning("mun-shell: the standby picture stays: the framebuffer took %zu of %zu bytes", done, black.size());
    else
        qInfo("mun-shell: the standby picture is cleared from the framebuffer");
}

// The exit status when the interface cannot be loaded (EX_SOFTWARE): a
// failure the unit restarts and the journal shows. Not 1, which is how Qt's
// framebuffer platform ends after SIGTERM, a clean stop
// (deploy/mun-shell.service).
constexpr int kInterfaceFailedStatus = 70;

// The start-up sequence plays once per boot. The launcher stops and starts
// the shell around every game, and a return from a game must land on Home
// with the session result, never on the splash again. The runtime directory
// outlives those restarts (RuntimeDirectoryPreserve=yes in the unit) and a
// reboot clears it. Without a runtime directory the splash always plays.
bool firstStartThisBoot()
{
    const QString dir = qEnvironmentVariable("XDG_RUNTIME_DIR");
    if (dir.isEmpty())
        return true;
    QFile marker(dir + QStringLiteral("/started"));
    if (marker.exists())
        return false;
    if (marker.open(QIODevice::WriteOnly))
        marker.close();
    return true;
}

// MUN_SHELL_SOUND_WAIT: seconds within which a sound device that is not
// there yet is expected at the start of a boot (a board whose sound card
// comes up after the shell starts sets it in its unit). Unset or 0: none.
std::chrono::milliseconds expectedSoundWait()
{
    bool ok = false;
    const int seconds = qEnvironmentVariableIntValue("MUN_SHELL_SOUND_WAIT", &ok);
    return std::chrono::seconds(ok ? std::clamp(seconds, 0, 60) : 0);
}

} // namespace

int main(int argc, char *argv[])
{
    // Before the application exists: they name the settings file outside
    // systemd, and the display mode must be chosen before Qt opens the
    // display (displaymode.h), with the canvas scaled to it.
    QCoreApplication::setApplicationName(QStringLiteral("mun-shell"));
    QCoreApplication::setOrganizationName(QStringLiteral("MUN"));
    // A display driver can come up after the unit starts; without a display
    // the shell exits and systemd starts it again (displaymode.h).
    if (!display::awaitDisplay())
        return display::kNoDisplayStatus;
    bool soundsOn = true;
    {
        QSettings settings(ShellSettings::filePath(), QSettings::IniFormat);
        display::prepare(settings);
        soundsOn = ShellSettings::systemSoundsIn(settings);
    }
    const bool firstStart = firstStartThisBoot();
    // The sounds before the application, so that at the first start of a
    // boot the device plays its silence while the interface loads: the
    // start-up's sound must not be the first thing a display hears
    // (systemsounds.h). QML gets this instance; it outlives the engine.
    SystemSounds sounds(nullptr);
    SystemSounds::provide(&sounds);
    if (firstStart && soundsOn) {
        sounds.expectDevice(expectedSoundWait());
        // MUN_SHELL_SOUND_READY: a file the system writes once the expected
        // device is ready (a board's route to HDMI set); until then, within
        // the wait, the device is not opened. Unset: opened as soon as it is
        // there.
        sounds.openAfter(qEnvironmentVariable("MUN_SHELL_SOUND_READY"));
        sounds.setAwake(true);
    }
    QGuiApplication app(argc, argv);
    app.setApplicationVersion(QStringLiteral(MUN_SHELL_VERSION));
    // Qt has set the display's mode by now.
    display::reportApplied();

    // Archivo (a variable font: width and weight axes) for everything read and
    // Michroma for the short labels are compiled in, so the interface looks
    // the same wherever it runs. Characters they lack fall back to the
    // system's fonts (the image's fonts-inter).
    for (const QString &font : {QStringLiteral(":/qt/qml/MUN/Shell/fonts/Archivo-Variable.ttf"),
                                QStringLiteral(":/qt/qml/MUN/Shell/fonts/Michroma-Regular.ttf")}) {
        if (QFontDatabase::addApplicationFont(font) < 0)
            qWarning("mun-shell: could not load %s", qPrintable(font));
    }
    QFont base(QStringLiteral("Archivo"));
    base.setPixelSize(24);
    app.setFont(base);

    QQmlApplicationEngine engine;
    QObject::connect(&engine, &QQmlApplicationEngine::objectCreationFailed, &app,
                     [] { QCoreApplication::exit(kInterfaceFailedStatus); }, Qt::QueuedConnection);
    engine.rootContext()->setContextProperty(QStringLiteral("shellFirstStart"), firstStart);
    // Home's design is the orbital one, on a graphics API and on Qt Quick's
    // software renderer alike (there, as in the virtual console, its
    // background is drawn once and stays still). MUN_SHELL_DESIGN=previous
    // brings back the previous design, kept as the reference the checks
    // compare with; "orbital" asks for the default.
    const bool orbital = qEnvironmentVariable("MUN_SHELL_DESIGN") != QLatin1String("previous");
    engine.rootContext()->setContextProperty(QStringLiteral("shellOrbital"), orbital);
    qInfo("mun-shell: design: %s", orbital ? "orbital" : "previous");

    // Optional UI iteration path: load QML from a directory instead of the
    // compiled resources. Production deployments leave this unset.
    const QString qmlDir = qEnvironmentVariable("MUN_SHELL_QML_DIR");
    if (!qmlDir.isEmpty() && QDir(qmlDir).exists()) {
        engine.addImportPath(QDir(qmlDir).absolutePath() + QStringLiteral("/.."));
        engine.load(QUrl::fromLocalFile(QDir(qmlDir).absoluteFilePath(QStringLiteral("Main.qml"))));
    } else {
        engine.loadFromModule("MUN.Shell", "Main");
    }
    if (engine.rootObjects().isEmpty())
        return kInterfaceFailedStatus;
    // At every start while the system's standby picture may still be in the
    // framebuffer: a start that ended before its first frame left it there.
    if (QFile::exists(QStringLiteral("/run/mun-standby/shown"))) {
        if (auto *window = qobject_cast<QQuickWindow *>(engine.rootObjects().constFirst()))
            QObject::connect(window, &QQuickWindow::frameSwapped, &app, &clearStandbyPicture,
                             Qt::ConnectionType(Qt::QueuedConnection | Qt::SingleShotConnection));
    }
    // Gamepads drive the menus as the keyboard does (src/gamepads.h); one
    // that comes or goes sounds, with the interface sounds on.
    Gamepads gamepads(engine.singletonInstance<FrameWatch *>("MUN.Shell", "FrameWatch"));
    auto *settings = engine.singletonInstance<ShellSettings *>("MUN.Shell", "ShellSettings");
    QObject::connect(&gamepads, &Gamepads::connected, [settings, &sounds] {
        if (settings && settings->systemSounds())
            sounds.play(QStringLiteral("controller_connect"));
    });
    QObject::connect(&gamepads, &Gamepads::disconnected, [settings, &sounds] {
        if (settings && settings->systemSounds())
            sounds.play(QStringLiteral("controller_disconnect"));
    });
    return app.exec();
}
