// SystemSounds plays the interface's sounds: moving, entering and going back
// on the menus (qml/Main.qml asks for them by name: "move", "enter", "back")
// and the start-up ("startup", qml/BootLayer.qml, which draws in step with
// it: started() says when its first samples went to the device, latency how
// long the device holds them before they are heard). They are compiled in
// (sounds/*.wav: PCM, 16-bit, 48 kHz, stereo), the menus' sounds mixed at
// 3/4 of their level and the start-up at its own, and played through
// ALSA's default device, the one the games use, which
// converts them if the device runs at another rate or format; the
// shell has it only while it runs, and the launcher stops the shell before a
// game starts.
//
// Playing never blocks the GUI thread: play() queues the sound and a worker
// thread mixes whatever is sounding (up to kVoices at once, so quick moves
// overlap instead of cutting each other) into the device. The device is
// opened on the first sound, fed silence between sounds so that it never
// runs dry, and closed after kIdle of silence. A device that fails is opened
// afresh at once; no device, or one that fails again, means silence and one
// line in the journal, never an error in the interface, and the next sound
// tries again after kRetry.
//
// stop() fades a sound out over kFadeFrames instead of cutting it.
//
// A Game Card's MUN Shape package may bring its own menu sounds and an
// insertion cue (docs/shape.md): useGameSounds() reads them from the card
// service's export, in the same format and within the contract's durations
// (the set is taken whole or not at all), and while `gameSounds` is on
// play("move") and the others play the game's; playGame("insert") plays the
// cue. The start-up and a card without sounds keep MUN's. A set is replaced
// on the GUI thread; a sound already queued or playing holds its samples
// (shared) until it ends.
//
// Thread contract: play(), stop(), useGameSounds() and playGame() on the GUI
// thread, started() delivered on it; the worker owns the ALSA handle and is
// joined by the destructor, before any member it reads is destroyed.
#pragma once

#include <QByteArray>
#include <QObject>
#include <QString>
#include <QStringList>
#include <QVariantMap>
#include <QtQml/qqmlregistration.h>

#include <chrono>
#include <condition_variable>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

class SystemSounds : public QObject {
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON
    // Milliseconds between a sample's write and its being heard.
    Q_PROPERTY(int latency READ latency CONSTANT)
    // Whether the menus play the game's sounds when it has some (the
    // player's "Game sounds on the menus" and MUN Shape in its full mode).
    Q_PROPERTY(bool gameSounds READ gameSounds WRITE setGameSounds NOTIFY gameSoundsChanged)
    // The game's set is loaded: the names it has.
    Q_PROPERTY(QStringList gameSet READ gameSet NOTIFY gameSetChanged)

public:
    explicit SystemSounds(QObject *parent = nullptr);
    ~SystemSounds() override;

    // Queues one of the named sounds; unknown names are ignored.
    Q_INVOKABLE void play(const QString &name);
    // Fades out the named sound wherever it is playing.
    Q_INVOKABLE void stop(const QString &name);
    // The game's sounds: name -> file of the card service's export ("move",
    // "enter", "back", optionally "insert"). An empty map, or a set with any
    // file out of the format or its duration, leaves MUN's.
    Q_INVOKABLE void useGameSounds(const QVariantMap &files);
    // One of the game's sounds only ("insert"), whenever the caller asks
    // (the caller checks the player's choices); nothing if it has none.
    Q_INVOKABLE void playGame(const QString &name);
    int latency() const { return int(kLatencyMicroseconds / 1000); }
    bool gameSounds() const { return m_gameSounds; }
    void setGameSounds(bool on);
    QStringList gameSet() const;

signals:
    // The first samples of a sound that play() asked for went to the device.
    void started(const QString &name);
    void gameSoundsChanged();
    void gameSetChanged();
    // One of the game's sounds was queued by playGame().
    void gameSoundQueued(const QString &name);

private:
    struct Sound {
        QString name;
        std::vector<qint16> samples;  // interleaved stereo frames
        int gain;                     // in quarters of the recorded level
    };
    using SoundPtr = std::shared_ptr<const Sound>;
    struct Voice {
        SoundPtr sound;  // held while it plays, whatever set replaces it
        size_t sample;   // next sample to mix
        size_t fade;     // frames left of a fade-out; 0 while not fading
    };

    // What ALSA may buffer ahead: short enough that a sound follows its key.
    static constexpr unsigned kLatencyMicroseconds = 40000;
    static constexpr int kRate = 48000;
    static constexpr int kChannels = 2;
    static constexpr size_t kVoices = 4;
    static constexpr int kPeriodFrames = kRate / 100;  // 10 ms mixed per write
    static constexpr size_t kFadeFrames = kRate / 10;   // 100 ms
    static constexpr std::chrono::seconds kIdle{3};
    static constexpr std::chrono::seconds kRetry{5};

    static SoundPtr parse(const QByteArray &wav, const QString &name, int gain, double maxSeconds);
    void load(const QString &name, int gain);
    void queue(const SoundPtr &sound);
    void run();

    std::vector<SoundPtr> m_sounds;         // MUN's: filled before the worker starts, then read-only
    std::map<QString, SoundPtr> m_game;     // the game's: GUI thread only
    bool m_gameSounds = true;
    std::mutex m_mutex;
    std::condition_variable m_wake;
    std::deque<SoundPtr> m_requests;  // guarded by m_mutex
    std::deque<SoundPtr> m_stops;     // guarded by m_mutex
    bool m_stop = false;              // guarded by m_mutex
    std::thread m_worker;
};
