// SystemSounds plays the interface's sounds: moving, entering and going back
// on the menus (qml/Main.qml asks for them by name: "move", "enter", "back"),
// the console's events (a game launched or returned from, Shape leaving, a
// safe release done, turning off, a success, warning or error, a controller
// connected or disconnected) and the start-up ("startup", qml/BootLayer.qml,
// which draws in step with it: started() says when its first samples went to
// the device, heard() how much of it has been heard, the samples written
// less what the device still holds). They are compiled in (sounds/*.wav:
// PCM, 16-bit, 48 kHz, stereo), the menus' and the events' mixed at 3/4 of
// their level and the start-up at its own, and played through ALSA's default
// device, the one the games use, which converts them if the device runs at
// another rate or format; the shell has it only while it runs, and the
// launcher stops the shell before a game starts.
//
// Playing never blocks the GUI thread: play() queues the sound and a worker
// thread mixes whatever is sounding (up to kVoices at once, so quick moves
// overlap instead of cutting each other) into the device. The device is
// opened on the first sound, fed silence between sounds so that it never
// runs dry, and closed after kIdle of silence, except while `awake`: then it
// is opened at once and kept open, fed silence, however long the silence.
// An HDMI display that loses the stream may need a moment to hear it again
// when it starts, and the first sounds after a pause, short as they are,
// would be lost in it (measured on a board: opening takes a few
// milliseconds, and nothing failed). For the same reason the silence fed
// between sounds is a noise far below hearing (at most 2 of 32768, about
// -84 dBFS) rather than exact zeros: a display may also stop listening
// to a stream of zeros after a while. Kept open it costs about one percent
// of a core on a board. The shell says awake while its interface is shown
// and its sounds are on, dimmed too (a stream opened again by the input
// that undims was not heard in time for the move pressed right after it),
// and the device is let go when the shell stops for a game. A device that
// fails is opened afresh at once; no device, or one that fails again, means
// silence and one line in the journal (after ALSA's own about that failure),
// never an error in the interface; it is tried again after kRetry, by the
// next sound or, awake, by itself, and those tries say nothing, ALSA's
// messages included, until a device plays. With MUN_SHELL_TIMING set, the
// journal says when the device is opened and let go.
//
// The start-up sound is the one case where that moment is never available
// later: a display that was given no stream before it would swallow its
// first notes while the picture already moves (measured on a board's HDMI
// display: about two seconds). So at the first start of a boot main() makes
// the one instance before the application, awake, and the device plays its
// silence while the interface loads; the start-up waits until the device
// has played well for long enough (playingFor(), BootLayer.qml). Open is not
// enough: after every write the worker checks that what the device has
// played advances at its rate (src/playbackhealth.h: not stalled, slow, fast
// or going back, and able to say what it holds).
//
// Where the sound card comes up after the shell starts (a board's audio
// DSP), expectDevice() says one is coming: until then a device that is not
// there is tried every kExpectRetry instead of kRetry, those attempts stay
// out of the journal (ALSA's own messages included; the kinds of failure
// are said once each), and openAfter() may name a file whose existence says
// the system has the device ready (a board's route to HDMI set), before which
// it is not opened. A device that fails after it opened is always reported.
//
// The journal follows the device (times in seconds since boot, few lines):
// when the shell starts looking for it, when it opens and after how many
// attempts, when it starts playing and when it plays at its rate, the first
// problems seen while it plays, a failed write with its error and the
// recovery's, a failed query of what it holds, and when it is let go.
// recoveries() counts the times an open device failed (a write that needed
// recovering, or the device opened afresh): the start-up stops following a
// sound whose device keeps recovering (src/startupclock.h). A device that
// opens and fails without playing anything, twice in a row (on a board: its
// route to the display not set), is not opened without end: it is tried
// again after kRetry (kExpectRetry while one is expected), and the journal
// says so once and nothing more until it plays.
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
// A sound the worker has to drop (no device, or one that stopped and failed
// again) is over: heard() says NaN for it from then on, and dropped() says
// so, as it does for a sound asked for while the device is being retried.
//
// Thread contract: play(), stop(), useGameSounds(), playGame(), heard(),
// playingFor(), deviceOpen(), sinceFirstOpen(), recoveries() and expecting() on
// the GUI thread
// (heard() reads what the worker wrote under a lock, the others atomics),
// started() and dropped() delivered on it; expectDevice(), openAfter() and
// setAwake() also on the thread that makes the instance before the
// application exists. The worker owns the
// ALSA handle and is joined by the destructor, before any member it reads is
// destroyed.
#pragma once

#include <QByteArray>
#include <QObject>
#include <QString>
#include <QStringList>
#include <QVariantMap>
#include <QtQml/qqmlregistration.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

class QJSEngine;
class QQmlEngine;

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
    // The interface is in use: the device is opened now and kept open
    // between sounds (see above). False (the default): let go after kIdle.
    Q_PROPERTY(bool awake READ awake WRITE setAwake NOTIFY awakeChanged)

public:
    // No default argument: QML makes a default-constructible singleton
    // itself, without asking create(), and would play through a second
    // instance beside main()'s.
    explicit SystemSounds(QObject *parent);
    ~SystemSounds() override;

    // The instance QML gets: the one main() made and provided (owned by
    // main(), outliving the engine), or a new one the engine owns.
    static void provide(SystemSounds *one);
    static SystemSounds *create(QQmlEngine *, QJSEngine *);

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
    // Seconds of the named sound heard so far: what went to the device less
    // what the device still holds (snd_pcm_delay), as of the last write and
    // carried on by the clock since, never past what was written. Below zero
    // while its first samples wait in the device; NaN when it is not
    // playing (not yet started, played out, or dropped). A device that stalls
    // leaves it where it was until it plays again or the sound is dropped.
    Q_INVOKABLE double heard(const QString &name) const;
    // Seconds the device has been open and fed without a break; below zero
    // while it is closed.
    // Seconds the device has been playing well (see above); below zero when
    // it is not: closed, not playing yet, or since a problem.
    Q_INVOKABLE double playingFor() const;
    // The device is open (playing well or not).
    Q_INVOKABLE bool deviceOpen() const;
    // Seconds since the device was first opened since the shell started,
    // whatever became of it; below zero if it never was.
    Q_INVOKABLE double sinceFirstOpen() const;
    // Times an open device failed since the shell started (see above).
    Q_INVOKABLE int recoveries() const;
    // Within expectDevice()'s wait: a device may still come, or recover.
    Q_INVOKABLE bool expecting() const;
    // A sound device is expected within `wait`, though there may be none yet
    // (see above). Zero or less: none is expected.
    void expectDevice(std::chrono::milliseconds wait);
    // While a device is expected, it is opened only once `path` exists (the
    // system says the device is ready). Empty: no such wait.
    void openAfter(const QString &path);
    int latency() const { return int(kLatencyMicroseconds / 1000); }
    bool gameSounds() const { return m_gameSounds; }
    void setGameSounds(bool on);
    QStringList gameSet() const;
    bool awake() const { return m_awake; }
    void setAwake(bool awake);

signals:
    // play() queued a sound (MUN's or the game's version), whether or not a
    // device will play it.
    void queued(const QString &name);
    // The first samples of a sound that play() asked for went to the device.
    void started(const QString &name);
    // A sound that play() asked for will not be heard (any more): no device.
    void dropped(const QString &name);
    void gameSoundsChanged();
    void gameSetChanged();
    void awakeChanged();
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
    static constexpr std::chrono::milliseconds kExpectRetry{250};
    // No advance for this long: the device is not playing (src/playbackhealth.h).
    static constexpr double kHealthGap = 0.25;

    static SoundPtr parse(const QByteArray &wav, const QString &name, int gain, double maxSeconds);
    void load(const QString &name, int gain);
    void queue(const SoundPtr &sound);
    void run();

    // How far a sound playing has gone, for heard().
    struct Progress {
        qint64 written = 0;   // frames gone to the device
        qint64 heard = 0;     // frames heard, as of `at`
        std::chrono::steady_clock::time_point at;
        bool done = false;    // played out or faded: nothing more is written
    };

    std::vector<SoundPtr> m_sounds;         // MUN's: filled before the worker starts, then read-only
    std::map<QString, SoundPtr> m_game;     // the game's: GUI thread only
    bool m_gameSounds = true;
    std::mutex m_mutex;
    std::condition_variable m_wake;
    std::deque<SoundPtr> m_requests;  // guarded by m_mutex
    std::deque<SoundPtr> m_stops;     // guarded by m_mutex
    bool m_stop = false;              // guarded by m_mutex
    bool m_awake = false;             // the GUI thread's copy
    bool m_keepOpen = false;          // guarded by m_mutex: the worker's
    mutable std::mutex m_progressMutex;
    std::map<QString, Progress> m_progress;  // guarded by m_progressMutex: the worker writes, heard() reads
    // steady_clock ticks: when the open device was opened (0: closed), the
    // worker's; until when a device is expected (0: none), expectDevice()'s.
    std::atomic<std::chrono::steady_clock::rep> m_openedAt{0};
    std::atomic<std::chrono::steady_clock::rep> m_expectUntil{0};
    // The worker's PlaybackHealth, published: seconds on the steady clock
    // since it played well and when it last advanced (below zero: never).
    std::atomic<double> m_healthySince{-1};
    std::atomic<double> m_lastAdvanceAt{-1};
    std::atomic<int> m_recoveries{0};
    std::atomic<double> m_firstOpenedAt{-1};
    std::string m_readyFile;  // guarded by m_mutex: openAfter()'s
    std::thread m_worker;
};
