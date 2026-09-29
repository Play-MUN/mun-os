// SystemSounds plays the interface's sounds for moving, entering and going
// back on the menus (qml/Main.qml asks for them by name: "move", "enter",
// "back"). They are compiled in (sounds/*.wav: PCM, 16-bit, 48 kHz, stereo)
// and played through ALSA's default device, the one the games use, which
// converts them if the device runs at another rate or format; the
// shell has it only while it runs, and the launcher stops the shell before a
// game starts.
//
// Playing never blocks the GUI thread: play() queues the sound and a worker
// thread mixes whatever is sounding (up to kVoices at once, so quick moves
// overlap instead of cutting each other) into the device. The device is
// opened on the first sound and closed after kIdle of silence. No device,
// or one that fails, means silence and one line in the journal, never an
// error in the interface; the next sound tries again after kRetry.
//
// Thread contract: play() on the GUI thread; the worker owns the ALSA handle
// and is joined by the destructor, before any member it reads is destroyed.
#pragma once

#include <QByteArray>
#include <QObject>
#include <QString>
#include <QtQml/qqmlregistration.h>

#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>
#include <vector>

class SystemSounds : public QObject {
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

public:
    explicit SystemSounds(QObject *parent = nullptr);
    ~SystemSounds() override;

    // Queues one of the named sounds; unknown names are ignored.
    Q_INVOKABLE void play(const QString &name);

private:
    struct Sound {
        QString name;
        std::vector<qint16> samples;  // interleaved stereo frames
    };
    struct Voice {
        const Sound *sound;
        size_t sample;  // next sample to mix
    };

    static constexpr int kRate = 48000;
    static constexpr int kChannels = 2;
    static constexpr size_t kVoices = 4;
    static constexpr int kPeriodFrames = kRate / 100;  // 10 ms mixed per write
    static constexpr std::chrono::seconds kIdle{3};
    static constexpr std::chrono::seconds kRetry{5};

    void load(const QString &name);
    void run();

    std::vector<Sound> m_sounds;  // filled before the worker starts, then read-only
    std::mutex m_mutex;
    std::condition_variable m_wake;
    std::deque<const Sound *> m_requests;  // guarded by m_mutex
    bool m_stop = false;                   // guarded by m_mutex
    std::thread m_worker;
};
