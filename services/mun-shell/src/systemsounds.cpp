#include "systemsounds.h"

#include <QFile>
#include <QMetaObject>
#include <QStringList>
#include <QtEndian>

#include <alsa/asoundlib.h>

#include <algorithm>

namespace {

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

} // namespace

SystemSounds::SystemSounds(QObject *parent) : QObject(parent)
{
    // Filled now and never again.
    m_sounds.reserve(4);
    for (const char *name : {"move", "enter", "back"})
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
    m_worker.join();
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

void SystemSounds::playGame(const QString &name)
{
    const auto it = m_game.find(name);
    if (m_gameSounds && it != m_game.end())
        queue(it->second);
}

void SystemSounds::play(const QString &name)
{
    // The game's version of a menu sound, while it has one and it is wanted.
    const auto game = m_game.find(name);
    if (m_gameSounds && game != m_game.end() && name != QLatin1String("insert")) {
        queue(game->second);
        return;
    }
    for (const SoundPtr &sound : m_sounds) {
        if (sound->name == name) {
            queue(sound);
            return;
        }
    }
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
    snd_pcm_t *pcm = nullptr;
    std::vector<Voice> voices;
    std::vector<int> mix(size_t(kPeriodFrames * kChannels));
    std::vector<qint16> period(mix.size());
    std::vector<QString> begun;  // sounds whose first samples this period holds
    std::chrono::steady_clock::time_point retryAt{}, lastSound{};
    bool reported = false;
    int failures = 0;  // writes failed in a row

    const auto closeDevice = [&] {
        if (pcm) {
            snd_pcm_close(pcm);
            pcm = nullptr;
        }
    };
    const auto giveUp = [&](const char *what, int error) {
        if (!reported)
            qWarning("mun-shell: interface sounds off: %s: %s", what, snd_strerror(error));
        reported = true;
        closeDevice();
        voices.clear();
        failures = 0;
        retryAt = std::chrono::steady_clock::now() + kRetry;
    };

    for (;;) {
        {
            std::unique_lock lock(m_mutex);
            if (!pcm && voices.empty())
                m_wake.wait(lock, [this] { return m_stop || !m_requests.empty(); });
            if (m_stop)
                break;
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

        const auto now = std::chrono::steady_clock::now();
        if (!voices.empty()) {
            lastSound = now;
        } else if (pcm && now - lastSound >= kIdle) {
            // Silent for a while: let the device go. What is left in it is
            // silence, so it is dropped rather than played out.
            snd_pcm_drop(pcm);
            closeDevice();
            continue;
        }

        if (!pcm) {
            if (now < retryAt) {
                voices.clear();
                continue;
            }
            int error = snd_pcm_open(&pcm, "default", SND_PCM_STREAM_PLAYBACK, 0);
            if (error < 0) {
                pcm = nullptr;
                giveUp("no sound device", error);
                continue;
            }
            error = snd_pcm_set_params(pcm, SND_PCM_FORMAT_S16_LE, SND_PCM_ACCESS_RW_INTERLEAVED, kChannels, kRate,
                                       1, kLatencyMicroseconds);
            if (error < 0) {
                giveUp("the sound device refuses 16-bit 48 kHz stereo", error);
                continue;
            }
        }

        // One period of everything sounding, mixed and limited; silence when
        // nothing is. While the device is open it is never left to run dry:
        // a stream that stops and starts again between sounds is what the
        // laboratory's virtual device, played through the Mac's CoreAudio,
        // failed to resume from (EIO), where a fresh device plays.
        std::fill(mix.begin(), mix.end(), 0);
        begun.clear();
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
            if (voice.fade > 0 && (voice.fade -= count / kChannels) == 0)
                voice.sample = samples.size();  // faded out
        }
        voices.erase(std::remove_if(voices.begin(), voices.end(),
                                    [](const Voice &voice) { return voice.sample >= voice.sound->samples.size(); }),
                     voices.end());
        for (size_t i = 0; i < mix.size(); ++i)
            period[i] = qint16(std::clamp(mix[i] / kQuarters, -32768, 32767));

        // Blocking writes pace the loop to the device.
        const qint16 *next = period.data();
        snd_pcm_uframes_t left = kPeriodFrames;
        while (left > 0) {
            snd_pcm_sframes_t written = snd_pcm_writei(pcm, next, left);
            if (written < 0)
                written = snd_pcm_recover(pcm, int(written), 1);
            if (written < 0) {
                // A device that fails mid-stream is opened afresh at once,
                // the sounds keeping their place; one that fails again
                // right after opening means silence for kRetry.
                if (++failures > 1)
                    giveUp("the sound device stopped", int(written));
                else
                    closeDevice();
                break;
            }
            next += written * kChannels;
            left -= snd_pcm_uframes_t(written);
        }
        if (left == 0) {
            failures = 0;
            reported = false;
            for (const QString &name : begun)
                QMetaObject::invokeMethod(this, [this, name] { emit started(name); }, Qt::QueuedConnection);
        }
    }
    closeDevice();
}
