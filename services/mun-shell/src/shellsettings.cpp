#include "shellsettings.h"

#include "displaymode.h"

#include <QCoreApplication>
#include <QList>
#include <QStandardPaths>
#include <QStringList>
#include <QTimer>

namespace {

const QString kLanguageKey = QStringLiteral("interface/language");
const QString kClockKey = QStringLiteral("interface/clock");
const QString kSafeAreaKey = QStringLiteral("display/safe-area");
const QString kAutoOffKey = QStringLiteral("power/auto-off-hours");
const QString kSoundsKey = QStringLiteral("audio/system-sounds");
const QString kShapeKey = QStringLiteral("shape/mode");
const QString kMotionKey = QStringLiteral("display/reduce-motion");
const QString kGameSoundsKey = QStringLiteral("audio/game-sounds");

const QStringList kLanguages{QStringLiteral("en"), QStringLiteral("es")};
const QStringList kClockFormats{QStringLiteral("24h"), QStringLiteral("12h")};
const QStringList kOnOff{QStringLiteral("on"), QStringLiteral("off")};
const QStringList kOffOn{QStringLiteral("off"), QStringLiteral("on")};
const QStringList kShapeModes{QStringLiteral("full"), QStringLiteral("colours"), QStringLiteral("off")};
const QList<int> kSafeAreas{100, 97, 94, 91};
const QList<int> kAutoOffHours{0, 1, 3, 6};

// Long enough for the scene to fade out before the display goes dark.
constexpr int kRestartDelayMs = 600;

QString readChoice(const QSettings &file, const QString &key, const QStringList &allowed)
{
    const QString value = file.value(key).toString();
    return allowed.contains(value) ? value : allowed.first();
}

int readNumber(const QSettings &file, const QString &key, const QList<int> &allowed, int fallback)
{
    bool ok = false;
    const int value = file.value(key).toString().toInt(&ok);
    return ok && allowed.contains(value) ? value : fallback;
}

} // namespace

QString ShellSettings::filePath()
{
    // systemd sets STATE_DIRECTORY from StateDirectory= (a colon-separated
    // list when there are several; the unit names one).
    QString directory = qEnvironmentVariable("STATE_DIRECTORY").section(QLatin1Char(':'), 0, 0);
    if (directory.isEmpty())
        directory = QStandardPaths::writableLocation(QStandardPaths::AppConfigLocation);
    return directory + QStringLiteral("/settings.ini");
}

ShellSettings::ShellSettings(QObject *parent)
    : QObject(parent), m_file(std::make_unique<QSettings>(filePath(), QSettings::IniFormat))
{
    m_resolutionOnTrial = display::plan().trial;
    m_language = readChoice(*m_file, kLanguageKey, kLanguages);
    m_clockFormat = readChoice(*m_file, kClockKey, kClockFormats);
    m_safeArea = readNumber(*m_file, kSafeAreaKey, kSafeAreas, 100);
    m_systemSounds = readChoice(*m_file, kSoundsKey, kOnOff) == kOnOff.first();
    m_autoPowerOffHours = readNumber(*m_file, kAutoOffKey, kAutoOffHours, 1);
    m_shapeMode = readChoice(*m_file, kShapeKey, kShapeModes);
    m_reduceMotion = readChoice(*m_file, kMotionKey, kOffOn) == QLatin1String("on");
    m_gameSounds = readChoice(*m_file, kGameSoundsKey, kOnOff) == kOnOff.first();
    if (m_file->status() == QSettings::FormatError)
        m_lastError = QStringLiteral("%1 is not a valid settings file; the defaults apply").arg(path());
}

ShellSettings::~ShellSettings() = default;

QString ShellSettings::path() const
{
    return m_file->fileName();
}

void ShellSettings::setLanguage(const QString &language)
{
    if (!kLanguages.contains(language) || language == m_language)
        return;
    m_language = language;
    store(kLanguageKey, language);
}

void ShellSettings::setClockFormat(const QString &format)
{
    if (!kClockFormats.contains(format) || format == m_clockFormat)
        return;
    m_clockFormat = format;
    store(kClockKey, format);
}

void ShellSettings::setSafeArea(int percent)
{
    if (!kSafeAreas.contains(percent) || percent == m_safeArea)
        return;
    m_safeArea = percent;
    store(kSafeAreaKey, percent);
}

void ShellSettings::setSystemSounds(bool on)
{
    if (on == m_systemSounds)
        return;
    m_systemSounds = on;
    store(kSoundsKey, on ? kOnOff.first() : kOnOff.last());
}

void ShellSettings::setShapeMode(const QString &mode)
{
    if (!kShapeModes.contains(mode) || mode == m_shapeMode)
        return;
    m_shapeMode = mode;
    store(kShapeKey, mode);
}

void ShellSettings::setReduceMotion(bool on)
{
    if (on == m_reduceMotion)
        return;
    m_reduceMotion = on;
    store(kMotionKey, on ? QStringLiteral("on") : QStringLiteral("off"));
}

void ShellSettings::setGameSounds(bool on)
{
    if (on == m_gameSounds)
        return;
    m_gameSounds = on;
    store(kGameSoundsKey, on ? kOnOff.first() : kOnOff.last());
}

void ShellSettings::setAutoPowerOffHours(int hours)
{
    if (!kAutoOffHours.contains(hours) || hours == m_autoPowerOffHours)
        return;
    m_autoPowerOffHours = hours;
    store(kAutoOffKey, hours);
}

QString ShellSettings::activeResolution() const
{
    return display::plan().active;
}

QStringList ShellSettings::resolutions() const
{
    return display::plan().offered;
}

QString ShellSettings::nativeResolution() const
{
    return display::plan().preferred;
}

QString ShellSettings::resume() const
{
    return display::plan().resume;
}

void ShellSettings::changeResolution(const QString &resolution)
{
    const display::Plan &plan = display::plan();
    if (m_restarting || (resolution == plan.active && !m_resolutionOnTrial))
        return;
    if (resolution == plan.active) {
        keepResolution();
        return;
    }
    if (resolution == QStringLiteral("auto"))
        m_file->remove(display::kResolutionKey);
    else if (plan.offered.contains(resolution))
        m_file->setValue(display::kTrialKey, resolution);
    else
        return;
    sync();
    // A choice that was not written would not survive the restart.
    if (m_lastError.isEmpty())
        restart(QStringLiteral("resolution"));
}

void ShellSettings::keepResolution()
{
    if (!m_resolutionOnTrial)
        return;
    m_resolutionOnTrial = false;
    store(display::kResolutionKey, display::plan().active);
}

void ShellSettings::revertResolution()
{
    // The trial was taken out of the file when this start began.
    if (m_resolutionOnTrial && !m_restarting)
        restart(QStringLiteral("resolution"));
}

void ShellSettings::resetKeepingLanguage()
{
    m_clockFormat = kClockFormats.first();
    m_safeArea = 100;
    m_autoPowerOffHours = 1;
    m_systemSounds = true;
    m_shapeMode = kShapeModes.first();
    m_reduceMotion = false;
    m_gameSounds = true;
    m_resolutionOnTrial = false;
    m_file->remove(kClockKey);
    m_file->remove(kSafeAreaKey);
    m_file->remove(kAutoOffKey);
    m_file->remove(kSoundsKey);
    m_file->remove(kShapeKey);
    m_file->remove(kMotionKey);
    m_file->remove(kGameSoundsKey);
    m_file->remove(display::kResolutionKey);
    sync();
    emit changed();
    if (display::plan().active != QStringLiteral("auto") && m_lastError.isEmpty())
        restart(QStringLiteral("reset"));
}

void ShellSettings::restart(const QString &resume)
{
    display::setResume(resume);
    m_restarting = true;
    emit changed();
    QTimer::singleShot(kRestartDelayMs, qApp, [] { QCoreApplication::exit(display::kRestartStatus); });
}

void ShellSettings::store(const QString &key, const QVariant &value)
{
    m_file->setValue(key, value);
    sync();
    emit changed();
}

void ShellSettings::sync()
{
    m_file->sync();
    // status() keeps the first error; a format error found when reading is
    // repaired by this write, so only a failure to write counts now.
    const QString error = m_file->status() == QSettings::AccessError
                              ? QStringLiteral("could not write %1; the choice lasts until the shell restarts").arg(path())
                              : QString();
    if (error != m_lastError) {
        m_lastError = error;
        emit lastErrorChanged();
    }
}
