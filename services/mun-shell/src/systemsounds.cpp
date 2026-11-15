#include "systemsounds.h"

#include "playbackhealth.h"

#include <QFile>
#include <QJSEngine>
#include <QMetaObject>
#include <QStringList>
#include <QtEndian>

#include <alsa/asoundlib.h>

#include <algorithm>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <mutex>
#include <limits>

namespace {

// The worker's write as ~SystemSounds sees it. A device that never drains
// holds the worker in snd_pcm_writei, where no flag reaches it, and the
// shell then leaves without it: the object it writes for is destroyed while
// that write may still return. So this lives in static storage, beyond the
// object. Under the lock the worker says it is in a write, and on its way
// out of one learns whether it was left behind; if so it returns at once,
// touching nothing of the object.
std::mutex g_writeLock;
bool g_inWrite = false;
bool g_leftBehind = false;

// Gains in quarters: the mock-up's level for the menus' sounds, leaving
// headroom when they overlap; the start-up sound as it was mastered.
constexpr int kQuarters = 4;
constexpr int kMenuGain = 3;
constexpr int kStartupGain = 4;
// A game's sounds, as the Shape contract bounds them (docs/shape.md).
constexpr qint64 kGameSoundMaxBytes = 1024 * 1024;
constexpr double kGameMenuSeconds = 1.0;
constexpr double kGameInsertSeconds = 3.0;

quint16 u16(const QByteArray &bytes, qsizetype at)
{
    return qFromLittleEndian<quint16>(bytes.constData() + at);
}

quint32 u32(const QByteArray &bytes, qsizetype at)
{
    return qFromLittleEndian<quint32>(bytes.constData() + at);
}

SystemSounds *provided = nullptr;

// ALSA's messages, as its own handler prints them, except while a device is
// expected (its attempts to open one that is not there yet are not news) and
// once a failure has been reported: the device is then tried again without
// end, and ALSA's several lines about each attempt would fill the journal.
std::atomic<bool> alsaQuiet{false};

void alsaMessage(const char *file, int line, const char *function, int error, const char *format, ...)
{
    if (alsaQuiet.load(std::memory_order_relaxed))
        return;
    std::fprintf(stderr, "ALSA lib %s:%i:(%s) ", file, line, function);
    va_list arguments;
    va_start(arguments, format);
    std::vfprintf(stderr, format, arguments);
    va_end(arguments);
    if (error)
        std::fprintf(stderr, ": %s", snd_strerror(error));
    std::fputc('\n', stderr);
}

} // namespace

void SystemSounds::provide(SystemSounds *one)
{
    provided = one;
}

SystemSounds *SystemSounds::create(QQmlEngine *, QJSEngine *)
{
    if (!provided)
        return new SystemSounds(nullptr);
    QJSEngine::setObjectOwnership(provided, QJSEngine::CppOwnership);
    return provided;
}

void SystemSounds::expectDevice(std::chrono::milliseconds wait)
{
    m_expectUntil = wait > std::chrono::milliseconds::zero()
        ? (std::chrono::steady_clock::now() + wait).time_since_epoch().count()
        : 0;
    m_wake.notify_one();
}

bool SystemSounds::expecting() const
{
    const auto until = m_expectUntil.load();
    return until != 0 && std::chrono::steady_clock::now().time_since_epoch().count() < until;
}

void SystemSounds::openAfter(const QString &path)
{
    {
        std::lock_guard lock(m_mutex);
        m_readyFile = path.toStdString();
    }
    m_wake.notify_one();
}

double SystemSounds::playingFor() const
{
    const double since = m_healthySince.load(), last = m_lastAdvanceAt.load();
    const double now = std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
    // As PlaybackHealth::healthyFor: a device that stopped playing (its
    // writes blocked, so the worker says nothing) is not playing well.
    if (since < 0 || last < 0 || now - last > kHealthGap)
        return -1;
    return now - since;
}

double SystemSounds::sinceFirstOpen() const
{
    const double first = m_firstOpenedAt.load();
    if (first < 0)
        return -1;
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count() - first;
}

bool SystemSounds::deviceOpen() const
{
    return m_openedAt.load() != 0;
}

int SystemSounds::recoveries() const
{
    return m_recoveries.load();
}

SystemSounds::SystemSounds(QObject *parent) : QObject(parent)
{
    // Filled now and never again: the menus' three, the console's events
    // (at the menus' level: one set, mastered to sit together) and the
    // start-up's.
    m_sounds.reserve(14);
    for (const char *name : {"move", "enter", "back", "launch", "game_return", "shape_exit", "eject", "shutdown",
                             "success", "warning", "error", "controller_connect", "controller_disconnect"})
        load(QString::fromLatin1(name), kMenuGain);
    load(QStringLiteral("startup"), kStartupGain);
    m_worker = std::thread(&SystemSounds::run, this);
}

SystemSounds::~SystemSounds()
{
    if (!m_worker.joinable())
        return;
    {
        std::lock_guard lock(m_mutex);
        m_stop = true;
    }
    m_wake.notify_one();
    // A device that takes samples and never drains holds the worker in its
    // write, where no flag reaches it (measured with a sink nobody read: the
    // shell asked for a new resolution sat in join() with its screen black).
    // The shell is leaving, for a new resolution or a stop: it waits a moment
    // and goes without the worker rather than for ever. Leaving ends the
    // process, the blocked thread with it.
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    while (!m_finished && std::chrono::steady_clock::now() < deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    // Left behind only while it is in a write (g_writeLock above): anywhere
    // else it is on its way out, with the stop asked, and is waited for.
    bool leave = false;
    if (!m_finished) {
        std::lock_guard lock(g_writeLock);
        leave = g_leftBehind = g_inWrite;
    }
    if (leave) {
        qWarning("mun-shell: sound: the sound device holds a write and does not let go; the shell leaves without it");
        m_worker.detach();
    } else {
        m_worker.join();
    }
}

// A RIFF/WAVE with a PCM format chunk in the device's format and a data
// chunk, nothing assumed; `maxSeconds` bounds it (0: no bound). Null if not.
SystemSounds::SoundPtr SystemSounds::parse(const QByteArray &wav, const QString &name, int gain, double maxSeconds)
{
    bool format = false;
    QByteArray data;
    if (wav.size() >= 12 && wav.startsWith("RIFF") && wav.mid(8, 4) == "WAVE") {
        qsizetype at = 12;
        while (at + 8 <= wav.size()) {
            const QByteArray id = wav.mid(at, 4);
            const quint32 size = u32(wav, at + 4);
            const qsizetype body = at + 8;
            if (size > quint32(wav.size() - body))
                break;
            if (id == "fmt " && size >= 16)
                format = u16(wav, body) == 1 && u16(wav, body + 2) == kChannels && u32(wav, body + 4) == kRate
                         && u16(wav, body + 14) == 16;
            else if (id == "data")
                data = wav.mid(body, size);
            at = body + size + (size & 1);
        }
    }
    if (!format || data.isEmpty() || data.size() % (kChannels * 2) != 0)
        return nullptr;
    if (maxSeconds > 0 && double(data.size()) / (kRate * kChannels * 2) > maxSeconds)
        return nullptr;
    auto sound = std::make_shared<Sound>(Sound{name, std::vector<qint16>(size_t(data.size() / 2)), gain});
    for (size_t i = 0; i < sound->samples.size(); ++i)
        sound->samples[i] = qFromLittleEndian<qint16>(data.constData() + 2 * i);
    return sound;
}

void SystemSounds::load(const QString &name, int gain)
{
    // Compiled in, but read as any file would be.
    QFile file(QStringLiteral(":/qt/qml/MUN/Shell/sounds/%1.wav").arg(name));
    const QByteArray wav = file.open(QIODevice::ReadOnly) ? file.readAll() : QByteArray();
    SoundPtr sound = parse(wav, name, gain, 0);
    if (!sound) {
        qWarning("mun-shell: sound %s is missing or not 16-bit %d Hz stereo PCM; it stays silent", qPrintable(name), kRate);
        return;
    }
    m_sounds.push_back(std::move(sound));
}

void SystemSounds::useGameSounds(const QVariantMap &files)
{
    // The files are the card service's copy in RAM, at most 1 MiB each: read
    // here, parsed as MUN's own are. The set is whole or MUN's.
    std::map<QString, SoundPtr> set;
    bool whole = !files.isEmpty();
    for (auto it = files.cbegin(); whole && it != files.cend(); ++it) {
        const QString &name = it.key();
        const bool menu = name == QLatin1String("move") || name == QLatin1String("enter") || name == QLatin1String("back");
        if (!menu && name != QLatin1String("insert"))
            continue;
        QFile file(it.value().toString());
        QByteArray wav;
        if (file.open(QIODevice::ReadOnly) && file.size() <= kGameSoundMaxBytes)
            wav = file.read(kGameSoundMaxBytes);
        SoundPtr sound = parse(wav, name, kMenuGain, menu ? kGameMenuSeconds : kGameInsertSeconds);
        if (!sound) {
            qWarning("mun-shell: the game's sound %s is not usable; MUN's sounds stay", qPrintable(name));
            whole = false;
            break;
        }
        set[name] = std::move(sound);
    }
    whole = whole && set.count(QStringLiteral("move")) && set.count(QStringLiteral("enter")) && set.count(QStringLiteral("back"));
    if (!whole)
        set.clear();
    if (set.empty() && m_game.empty())
        return;
    m_game = std::move(set);
    emit gameSetChanged();
}

void SystemSounds::setGameSounds(bool on)
{
    if (on == m_gameSounds)
        return;
    m_gameSounds = on;
    emit gameSoundsChanged();
}

QStringList SystemSounds::gameSet() const
{
    QStringList names;
    for (const auto &entry : m_game)
        names << entry.first;
    return names;
}

void SystemSounds::queue(const SoundPtr &sound)
{
    {
        std::lock_guard lock(m_mutex);
        if (m_requests.size() < kVoices)
            m_requests.push_back(sound);
    }
    m_wake.notify_one();
}

void SystemSounds::setAwake(bool awake)
{
    if (awake == m_awake)
        return;
    m_awake = awake;
    {
        std::lock_guard lock(m_mutex);
        m_keepOpen = awake;
    }
    m_wake.notify_one();
    emit awakeChanged();
}

void SystemSounds::playGame(const QString &name)
{
    // Whenever the caller says (the insertion cue, as the identity begins to
    // come in): not bound to where the menus' sounds are the game's.
    const auto it = m_game.find(name);
    if (it != m_game.end()) {
        queue(it->second);
        emit gameSoundQueued(name);
    }
}

void SystemSounds::play(const QString &name)
{
    // The game's version of a menu sound, while it has one and it is wanted.
    const auto game = m_game.find(name);
    if (m_gameSounds && game != m_game.end() && name != QLatin1String("insert")) {
        queue(game->second);
        emit queued(name);
        return;
    }
    for (const SoundPtr &sound : m_sounds) {
        if (sound->name == name) {
            {
                std::lock_guard lock(m_progressMutex);
                m_progress.erase(name);   // a new play starts from nothing heard
            }
            queue(sound);
            emit queued(name);
            return;
        }
    }
}

double SystemSounds::heard(const QString &name) const
{
    std::lock_guard lock(m_progressMutex);
    const auto it = m_progress.find(name);
    if (it == m_progress.end())
        return std::numeric_limits<double>::quiet_NaN();
    const Progress &progress = it->second;
    const double since = std::chrono::duration<double>(std::chrono::steady_clock::now() - progress.at).count();
    const double frames = std::min(double(progress.written), double(progress.heard) + since * kRate);
    if (progress.done && frames >= double(progress.written))
        return std::numeric_limits<double>::quiet_NaN();
    return frames / kRate;
}

void SystemSounds::stop(const QString &name)
{
    for (const SoundPtr &sound : m_sounds) {
        if (sound->name != name)
            continue;
        {
            std::lock_guard lock(m_mutex);
            m_stops.push_back(sound);
        }
        m_wake.notify_one();
        return;
    }
}

void SystemSounds::run()
{
    using Clock = std::chrono::steady_clock;
    snd_pcm_t *pcm = nullptr;
    std::vector<Voice> voices;
    std::vector<int> mix(size_t(kPeriodFrames * kChannels));
    std::vector<qint16> period(mix.size());
    std::vector<QString> begun;  // sounds whose first samples this period holds
    struct Moved {
        QString name;
        qint64 written;
        bool done;
    };
    std::vector<Moved> moved;    // how far each sound in this period has gone
    Clock::time_point retryAt{}, lastSound{}, openedAt{}, lookedFrom{};
    bool reported = false;
    bool keepOpen = false;  // the worker's copy of m_keepOpen
    int failures = 0;  // failures since the device last played anything
    quint32 noise = 0x9E3779B9;  // xorshift state of the silence's noise
    static const bool timing = qEnvironmentVariableIsSet("MUN_SHELL_TIMING");
    snd_lib_error_set_handler(alsaMessage);

    // Seconds since boot (the steady clock is the monotonic one): the
    // journal's times line up with the kernel's and the other services'.
    const auto seconds = [](Clock::time_point at) { return std::chrono::duration<double>(at.time_since_epoch()).count(); };
    const auto expected = [this](Clock::time_point now) {
        const auto until = m_expectUntil.load();
        return until != 0 && now.time_since_epoch().count() < until;
    };

    // Looking for a device that is expected: since when, how many attempts,
    // the kinds of failure said so far (each once) and the last one.
    bool looked = false;
    int attempts = 0;
    std::vector<std::string> kinds;
    std::string lastFailure;
    bool readySaid = false;
    // The open device: what it played (PlaybackHealth), what was written to
    // it, and how much the journal has said about it (bounded).
    PlaybackHealth health(kRate, PlaybackHealth::Limits{0.5, 0.8, 1.25, kHealthGap});
    qint64 writtenSinceOpen = 0;
    bool advanceSaid = false, healthSaid = false;
    int problemLines = 0, writeLines = 0, delayLines = 0, problems = 0;
    // Openings in a row that ended without the device having played
    // anything: after two, the journal says no more about them until one
    // plays, and the device is tried again only after a wait.
    int deadOpens = 0;
    const auto loud = [&] { return deadOpens < 2; };
    const auto publish = [&] {
        m_healthySince = health.healthySince();
        m_lastAdvanceAt = health.lastAdvanceAt();
    };

    const auto closeDevice = [&](const char *why) {
        if (pcm) {
            // Said only for a device that was set up and played (not for one
            // closed again because it could not be set up).
            const bool wasOpen = m_openedAt.load() != 0;
            const bool played = health.advancingSince() >= 0;
            snd_pcm_close(pcm);
            pcm = nullptr;
            m_openedAt = 0;
            health.closed();
            publish();
            const auto now = Clock::now();
            if (wasOpen && (played || loud()))
                qInfo("mun-shell: sound: the device let go at %.2f s (%s) after %.2f s open, %d problem(s) seen",
                      seconds(now), why, std::chrono::duration<double>(now - openedAt).count(), problems);
            if (wasOpen)
                deadOpens = played ? 0 : deadOpens + 1;
        }
    };
    // Sounds that will not be heard: over for heard() (NaN once what was
    // heard has passed) and said to the GUI thread.
    const auto drop = [&] {
        if (voices.empty())
            return;
        {
            std::lock_guard lock(m_progressMutex);
            const auto now = Clock::now();
            for (const Voice &voice : voices) {
                Progress &progress = m_progress[voice.sound->name];
                const double since = std::chrono::duration<double>(now - progress.at).count();
                const qint64 heard = std::min(progress.written, progress.heard + qint64(since * kRate));
                progress = {heard, heard, now, true};
            }
        }
        for (const Voice &voice : voices) {
            const QString name = voice.sound->name;
            QMetaObject::invokeMethod(this, [this, name] { emit dropped(name); }, Qt::QueuedConnection);
        }
        voices.clear();
    };
    // `opening`: the device could not be opened or set up (it may not be
    // there yet); otherwise it failed after it had opened.
    const auto giveUp = [&](const char *what, int error, bool opening) {
        const auto now = Clock::now();
        const bool coming = expected(now);
        if (coming && opening) {
            // Not there yet: looked for again soon; each kind of failure
            // said once, the rest counted.
            ++attempts;
            if (!looked) {
                looked = true;
                lookedFrom = now;
                qInfo("mun-shell: sound: looking for the device from %.2f s", seconds(now));
            }
            lastFailure = std::string(what) + ": " + snd_strerror(error);
            if (std::find(kinds.begin(), kinds.end(), lastFailure) == kinds.end() && kinds.size() < 5) {
                kinds.push_back(lastFailure);
                qInfo("mun-shell: sound: not yet at %.2f s: %s", seconds(now), lastFailure.c_str());
            }
        } else if (!reported) {
            qWarning("mun-shell: interface sounds off at %.2f s: %s: %s%s", seconds(now), what, snd_strerror(error),
                     opening ? "" : "; it is tried again, quietly, until it plays");
            reported = true;
        }
        closeDevice("failed");
        drop();
        failures = 0;
        retryAt = now + (coming ? Clock::duration(kExpectRetry) : Clock::duration(kRetry));
    };

    for (;;) {
        std::string readyFile;
        {
            std::unique_lock lock(m_mutex);
            if (!pcm && voices.empty()) {
                // Closed: wait for a sound, or, awake, for the device to be
                // opened (at once, or once a failed one may be tried again).
                const auto ready = [this, &retryAt] {
                    return m_stop || !m_requests.empty() || (m_keepOpen && Clock::now() >= retryAt);
                };
                if (m_keepOpen && Clock::now() < retryAt)
                    m_wake.wait_until(lock, retryAt, ready);
                else
                    m_wake.wait(lock, ready);
            }
            if (m_stop)
                break;
            keepOpen = m_keepOpen;
            readyFile = m_readyFile;
            while (!m_requests.empty()) {
                if (voices.size() == kVoices)
                    voices.erase(voices.begin());  // the oldest makes room
                voices.push_back({m_requests.front(), 0, 0});
                m_requests.pop_front();
            }
            while (!m_stops.empty()) {
                for (Voice &voice : voices)
                    if (voice.sound->name == m_stops.front()->name && voice.fade == 0)
                        voice.fade = kFadeFrames;
                m_stops.pop_front();
            }
        }

        const auto now = Clock::now();
        if (!voices.empty()) {
            lastSound = now;
        } else if (pcm && !keepOpen && now - lastSound >= kIdle) {
            // Silent for a while, and not awake: let the device go. What is
            // left in it is silence, so it is dropped rather than played out.
            snd_pcm_drop(pcm);
            closeDevice("silence");
            continue;
        }

        if (!pcm) {
            if (now < retryAt) {
                drop();
                continue;
            }
            // While a device is expected, not before the system says it is
            // ready (openAfter()).
            if (!readyFile.empty() && expected(now)) {
                if (!QFile::exists(QString::fromStdString(readyFile))) {
                    if (!readySaid) {
                        readySaid = true;
                        qInfo("mun-shell: sound: waiting for %s before opening the device, from %.2f s",
                              readyFile.c_str(), seconds(now));
                    }
                    drop();
                    retryAt = now + kExpectRetry;
                    continue;
                }
            }
            // ALSA explains the first failure, which is reported; the
            // tries after it are quiet until a device plays.
            alsaQuiet = expected(now) || reported;
            int error = snd_pcm_open(&pcm, "default", SND_PCM_STREAM_PLAYBACK, 0);
            if (error < 0) {
                pcm = nullptr;
                giveUp("no sound device", error, true);
                continue;
            }
            error = snd_pcm_set_params(pcm, SND_PCM_FORMAT_S16_LE, SND_PCM_ACCESS_RW_INTERLEAVED, kChannels, kRate,
                                       1, kLatencyMicroseconds);
            if (error < 0) {
                giveUp("the sound device refuses 16-bit 48 kHz stereo", error, true);
                continue;
            }
            alsaQuiet = false;
            const auto opened = Clock::now();
            std::string how = voices.empty() ? "awake" : "for a sound";
            if (!readyFile.empty() && readySaid)
                how += ", " + readyFile + " there";
            if (looked) {
                qInfo("mun-shell: sound: the device opened at %.2f s (%s); the sound device came %.2f s after the shell "
                      "first looked for it, %d attempt(s) before, the last: %s",
                      seconds(opened), how.c_str(), std::chrono::duration<double>(opened - lookedFrom).count(), attempts,
                      lastFailure.c_str());
                looked = false;
                attempts = 0;
                kinds.clear();
            } else if (loud()) {
                qInfo("mun-shell: sound: the device opened at %.2f s (%s)", seconds(opened), how.c_str());
            }
            if (timing)
                qInfo("mun-shell: sound device opened in %lld ms",
                      qlonglong(std::chrono::duration_cast<std::chrono::milliseconds>(opened - now).count()));
            openedAt = opened;
            m_openedAt = opened.time_since_epoch().count();
            double never = -1;
            m_firstOpenedAt.compare_exchange_strong(never, seconds(opened));
            lastSound = opened;
            health.opened(seconds(opened));
            publish();
            writtenSinceOpen = 0;
            advanceSaid = healthSaid = false;
            problemLines = writeLines = delayLines = problems = 0;
        }

        // One period of everything sounding, mixed and limited; silence when
        // nothing is. While the device is open it is never left to run dry:
        // a stream that stops and starts again between sounds is what the
        // laboratory's virtual device, played through the Mac's CoreAudio,
        // failed to resume from (EIO), where a fresh device plays.
        std::fill(mix.begin(), mix.end(), 0);
        begun.clear();
        moved.clear();
        for (Voice &voice : voices) {
            const std::vector<qint16> &samples = voice.sound->samples;
            if (voice.sample == 0)
                begun.push_back(voice.sound->name);
            size_t count = std::min(mix.size(), samples.size() - voice.sample);
            if (voice.fade > 0)
                count = std::min(count, voice.fade * kChannels);
            for (size_t i = 0; i < count; ++i) {
                int value = samples[voice.sample + i] * voice.sound->gain;
                if (voice.fade > 0)  // linear, frame by frame, to silence
                    value = int(qint64(value) * qint64(voice.fade - i / kChannels) / qint64(kFadeFrames));
                mix[i] += value;
            }
            voice.sample += count;
            const qint64 reached = qint64(voice.sample / kChannels);
            if (voice.fade > 0 && (voice.fade -= count / kChannels) == 0)
                voice.sample = samples.size();  // faded out
            moved.push_back({voice.sound->name, reached, voice.sample >= samples.size()});
        }
        voices.erase(std::remove_if(voices.begin(), voices.end(),
                                    [](const Voice &voice) { return voice.sample >= voice.sound->samples.size(); }),
                     voices.end());
        if (moved.empty()) {
            // Nothing sounding: a noise far below hearing (at most 2 of
            // 32768) rather than exact zeros (see systemsounds.h).
            for (qint16 &sample : period) {
                noise ^= noise << 13;
                noise ^= noise >> 17;
                noise ^= noise << 5;
                sample = qint16(int(noise % 5) - 2);
            }
        } else {
            for (size_t i = 0; i < mix.size(); ++i)
                period[i] = qint16(std::clamp(mix[i] / kQuarters, -32768, 32767));
        }

        // Blocking writes pace the loop to the device.
        const qint16 *next = period.data();
        snd_pcm_uframes_t left = kPeriodFrames;
        while (left > 0) {
            {
                std::lock_guard lock(g_writeLock);
                g_inWrite = true;
            }
            snd_pcm_sframes_t written = snd_pcm_writei(pcm, next, left);
            {
                std::lock_guard lock(g_writeLock);
                if (g_leftBehind)
                    return;  // the shell left without this worker: its object is gone
                g_inWrite = false;
            }
            if (written < 0) {
                // A failed write: recovered where ALSA can (an underrun, a
                // suspend); both errors said, a few times per opening.
                const int original = int(written);
                written = snd_pcm_recover(pcm, original, 1);
                ++m_recoveries;
                if (writeLines < 5 && loud()) {
                    ++writeLines;
                    qWarning("mun-shell: sound: a write failed at %.2f s: %s; recovering it: %s", seconds(Clock::now()),
                             snd_strerror(original), written < 0 ? snd_strerror(int(written)) : "done");
                }
            }
            if (written < 0) {
                // A device that fails mid-stream is opened afresh at once,
                // the sounds keeping their place; one that fails again
                // without having played since means silence for kRetry
                // (kExpectRetry while one is expected) before the next try:
                // a device that takes a few periods and plays none (its
                // route not set) would otherwise be opened without end.
                if (++failures > 1)
                    giveUp("the sound device stopped", int(written), false);
                else
                    closeDevice("stopped");
                break;
            }
            next += written * kChannels;
            left -= snd_pcm_uframes_t(written);
            writtenSinceOpen += written;
        }
        if (left == 0) {
            // What the device still holds is not played yet: it says how far
            // it has got, for heard() and for its health.
            snd_pcm_sframes_t held = 0;
            const int delayError = snd_pcm_delay(pcm, &held);
            const auto at = Clock::now();
            if (delayError < 0 && delayLines < 3) {
                ++delayLines;
                qWarning("mun-shell: sound: the device could not say what it holds at %.2f s: %s", seconds(at),
                         snd_strerror(delayError));
            }
            const auto problem = health.wrote(seconds(at), writtenSinceOpen, delayError < 0 ? -1 : qint64(held));
            publish();
            if (!advanceSaid && health.advancingSince() >= 0) {
                advanceSaid = true;
                // It plays: a failure from here is a new one, and said.
                failures = 0;
                reported = false;
                if (deadOpens >= 2)
                    qInfo("mun-shell: sound: the device plays from %.2f s, after %d opening(s) that played nothing",
                          health.advancingSince(), deadOpens);
                else
                    qInfo("mun-shell: sound: the device plays from %.2f s, %.0f ms after it opened",
                          health.advancingSince(), (health.advancingSince() - health.openedAt()) * 1000);
                deadOpens = 0;
            }
            if (!healthSaid && health.healthySince() >= 0) {
                healthSaid = true;
                qInfo("mun-shell: sound: the device plays at its rate from %.2f s (%.2f times it)", health.healthySince(),
                      health.lastRate());
            }
            if (problem != PlaybackHealth::Problem::None) {
                ++problems;
                if (problemLines < 8) {
                    ++problemLines;
                    static const char *const names[] = {"", "stalled", "slow", "fast", "going back",
                                                         "unable to say what it holds"};
                    qWarning("mun-shell: sound: the device is %s at %.2f s (%.2f times its rate over the last window)",
                             names[int(problem)], seconds(at), health.lastRate());
                    healthSaid = false;  // said again when it plays at its rate again
                }
            }
            if (!moved.empty()) {
                if (delayError < 0 || held < 0)
                    held = snd_pcm_sframes_t(qint64(kLatencyMicroseconds) * kRate / 1000000);
                std::lock_guard lock(m_progressMutex);
                for (const Moved &m : moved)
                    m_progress[m.name] = {m.written, m.written - qint64(held), at, m.done};
            }
            for (const QString &name : begun)
                QMetaObject::invokeMethod(this, [this, name] { emit started(name); }, Qt::QueuedConnection);
        }
    }
    closeDevice("the shell stops");
    m_finished = true;
}
