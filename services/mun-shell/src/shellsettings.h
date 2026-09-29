// ShellSettings keeps the player's choices across restarts and reboots: the
// interface language, the clock format, the resolution, the safe area and the
// automatic power off. They live in an INI file in the service's state directory
// ($STATE_DIRECTORY, /var/lib/mun-shell in the image); outside systemd, in the
// user's configuration directory. Nothing else is stored: games and saves
// live on their Game Cards.
//
// The file is input like any other: an unknown or malformed value reads as
// its default, and a choice outside the allowed set is refused. Every change
// is written at once (QSettings writes a temporary file and renames it over
// the old one); a write that fails keeps the choice for this session and
// reports it in `lastError`. GUI thread only.
//
// The resolution applies from the next start (displaymode.h): choosing one
// restarts the shell, and a new mode stays only if the player keeps it.
#pragma once

#include <QObject>
#include <QSettings>
#include <QString>
#include <QStringList>
#include <QtQml/qqmlregistration.h>

#include <memory>

class ShellSettings : public QObject {
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    // "en" (the default) or "es".
    Q_PROPERTY(QString language READ language WRITE setLanguage NOTIFY changed)
    // "24h" (the default) or "12h".
    Q_PROPERTY(QString clockFormat READ clockFormat WRITE setClockFormat NOTIFY changed)
    // Percentage of the screen the interface uses: 100 (the default), 97, 94 or 91.
    Q_PROPERTY(int safeArea READ safeArea WRITE setSafeArea NOTIFY changed)
    // Hours without input on the console's menus before it turns itself off:
    // 0 (never), 1 (the default), 3 or 6. A game in progress does not count.
    Q_PROPERTY(int autoPowerOffHours READ autoPowerOffHours WRITE setAutoPowerOffHours NOTIFY changed)
    // The resolution this start asked for: "auto" (the display's own mode)
    // or a mode, "2560x1440"; the modes the display offers, smallest first
    // (none: the resolution cannot be chosen here); the display's own mode.
    Q_PROPERTY(QString activeResolution READ activeResolution CONSTANT)
    Q_PROPERTY(QStringList resolutions READ resolutions CONSTANT)
    Q_PROPERTY(QString nativeResolution READ nativeResolution CONSTANT)
    // The active resolution is new and waits for keepResolution().
    Q_PROPERTY(bool resolutionOnTrial READ resolutionOnTrial NOTIFY changed)
    // Where the start before this one asked to return: "resolution" (a
    // change of resolution), "reset" (settings reset) or "".
    Q_PROPERTY(QString resume READ resume CONSTANT)
    // The shell is about to restart; input is over.
    Q_PROPERTY(bool restarting READ restarting NOTIFY changed)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)
    Q_PROPERTY(QString path READ path CONSTANT)

public:
    explicit ShellSettings(QObject *parent = nullptr);
    ~ShellSettings() override;

    QString language() const { return m_language; }
    void setLanguage(const QString &language);
    QString clockFormat() const { return m_clockFormat; }
    void setClockFormat(const QString &format);
    int safeArea() const { return m_safeArea; }
    void setSafeArea(int percent);
    int autoPowerOffHours() const { return m_autoPowerOffHours; }
    void setAutoPowerOffHours(int hours);
    QString activeResolution() const;
    QStringList resolutions() const;
    QString nativeResolution() const;
    bool resolutionOnTrial() const { return m_resolutionOnTrial; }
    QString resume() const;
    bool restarting() const { return m_restarting; }
    QString lastError() const { return m_lastError; }
    QString path() const;
    // The settings file, for the display's choice before the application
    // exists (main.cpp).
    static QString filePath();

    // "auto" is kept at once; a mode is tried. Either restarts the shell.
    Q_INVOKABLE void changeResolution(const QString &resolution);
    // The mode on trial becomes the choice.
    Q_INVOKABLE void keepResolution();
    // Restarts with the choice before the trial.
    Q_INVOKABLE void revertResolution();

    // Back to the defaults, except the language: whoever resets keeps reading
    // the console in theirs. A resolution other than "auto" restarts the
    // shell.
    Q_INVOKABLE void resetKeepingLanguage();

signals:
    void changed();
    void lastErrorChanged();

private:
    void store(const QString &key, const QVariant &value);
    void sync();
    void restart(const QString &resume);

    std::unique_ptr<QSettings> m_file;
    QString m_language;
    QString m_clockFormat;
    int m_safeArea = 100;
    int m_autoPowerOffHours = 1;
    bool m_resolutionOnTrial = false;
    bool m_restarting = false;
    QString m_lastError;
};
