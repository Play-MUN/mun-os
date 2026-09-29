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
#include <QSettings>

#include "displaymode.h"
#include "shellsettings.h"

namespace {

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

} // namespace

int main(int argc, char *argv[])
{
    // Before the application exists: they name the settings file outside
    // systemd, and the display mode must be chosen before Qt opens the
    // display (displaymode.h), with the canvas scaled to it.
    QCoreApplication::setApplicationName(QStringLiteral("mun-shell"));
    QCoreApplication::setOrganizationName(QStringLiteral("MUN"));
    {
        QSettings settings(ShellSettings::filePath(), QSettings::IniFormat);
        display::prepare(settings);
    }
    QGuiApplication app(argc, argv);
    app.setApplicationVersion(QStringLiteral(MUN_SHELL_VERSION));

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
                     [] { QCoreApplication::exit(1); }, Qt::QueuedConnection);
    engine.rootContext()->setContextProperty(QStringLiteral("shellFirstStart"), firstStartThisBoot());

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
        return 1;
    return app.exec();
}
