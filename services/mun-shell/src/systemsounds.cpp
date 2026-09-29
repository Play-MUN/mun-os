#include "systemsounds.h"

#include <QFile>
#include <QtEndian>

#include <alsa/asoundlib.h>

#include <algorithm>

namespace {

// The mock-up's level for these sounds, leaving headroom when they overlap.
constexpr int kGainNumerator = 3;
constexpr int kGainDenominator = 4;
// What ALSA may buffer ahead: short enough that a sound follows its key.
constexpr unsigned kLatencyMicroseconds = 40000;

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
    // Filled now and never again: the worker keeps pointers into it.
    m_sounds.reserve(3);
    for (const char *name : {"move", "enter", "back"})
        load(QString::fromLatin1(name));
    if (!m_sounds.empty())
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

void SystemSounds::load(const QString &name)
{
    // Compiled in, but read as any file would be: a RIFF/WAVE with a PCM
    // format chunk in the device's format and a data chunk, nothing assumed.
    QFile file(QStringLiteral(":/qt/qml/MUN/Shell/sounds/%1.wav").arg(name));
    const QByteArray wav = file.open(QIODevice::ReadOnly) ? file.readAll() : QByteArray();
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
    if (!format || data.isEmpty() || data.size() % (kChannels * 2) != 0) {
        qWarning("mun-shell: sound %s is missing or not 16-bit %d Hz stereo PCM; it stays silent", qPrintable(name), kRate);
        return;
    }
    Sound sound{name, std::vector<qint16>(size_t(data.size() / 2))};
    for (size_t i = 0; i < sound.samples.size(); ++i)
        sound.samples[i] = qFromLittleEndian<qint16>(data.constData() + 2 * i);
    m_sounds.push_back(std::move(sound));
}

void SystemSounds::play(const QString &name)
{
    for (const Sound &sound : m_sounds) {
        if (sound.name != name)
            continue;
        {
            std::lock_guard lock(m_mutex);
            if (m_requests.size() < kVoices)
                m_requests.push_back(&sound);
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
    std::chrono::steady_clock::time_point retryAt{};
    bool reported = false;

    const auto giveUp = [&](const char *what, int error) {
        if (!reported)
            qWarning("mun-shell: interface sounds off: %s: %s", what, snd_strerror(error));
        reported = true;
        if (pcm) {
            snd_pcm_close(pcm);
            pcm = nullptr;
        }
        voices.clear();
        retryAt = std::chrono::steady_clock::now() + kRetry;
    };

    for (;;) {
        {
            std::unique_lock lock(m_mutex);
            const auto woken = [this] { return m_stop || !m_requests.empty(); };
            if (voices.empty()) {
                if (!pcm) {
                    m_wake.wait(lock, woken);
                } else if (!m_wake.wait_for(lock, kIdle, woken)) {
                    // Silent for a while: let the device go.
                    snd_pcm_close(pcm);
                    pcm = nullptr;
                    continue;
                }
            }
            if (m_stop)
                break;
            while (!m_requests.empty()) {
                if (voices.size() == kVoices)
                    voices.erase(voices.begin());  // the oldest makes room
                voices.push_back({m_requests.front(), 0});
                m_requests.pop_front();
            }
        }

        if (!pcm) {
            if (std::chrono::steady_clock::now() < retryAt) {
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
            reported = false;
        }

        // One period of everything sounding, mixed and limited.
        std::fill(mix.begin(), mix.end(), 0);
        for (Voice &voice : voices) {
            const std::vector<qint16> &samples = voice.sound->samples;
            const size_t count = std::min(mix.size(), samples.size() - voice.sample);
            for (size_t i = 0; i < count; ++i)
                mix[i] += samples[voice.sample + i];
            voice.sample += count;
        }
        voices.erase(std::remove_if(voices.begin(), voices.end(),
                                    [](const Voice &voice) { return voice.sample >= voice.sound->samples.size(); }),
                     voices.end());
        for (size_t i = 0; i < mix.size(); ++i)
            period[i] = qint16(std::clamp(mix[i] * kGainNumerator / kGainDenominator, -32768, 32767));

        // Blocking writes pace the loop to the device. Running dry between
        // sounds is an underrun the next write recovers from.
        const qint16 *next = period.data();
        snd_pcm_uframes_t left = kPeriodFrames;
        while (left > 0 && pcm) {
            snd_pcm_sframes_t written = snd_pcm_writei(pcm, next, left);
            if (written < 0)
                written = snd_pcm_recover(pcm, int(written), 1);
            if (written < 0) {
                giveUp("the sound device stopped", int(written));
                break;
            }
            next += written * kChannels;
            left -= snd_pcm_uframes_t(written);
        }
    }
    if (pcm)
        snd_pcm_close(pcm);
}
