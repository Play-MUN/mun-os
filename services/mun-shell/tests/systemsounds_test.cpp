// The sound worker and the shell's exit (src/systemsounds.*), with ALSA
// replaced by the stand-ins below, linked instead of libasound. A write the
// test holds stands for a device that takes samples and never drains: the
// shell leaves without the worker (~SystemSounds waits a moment, then lets
// it go), and the object is destroyed. The test then lets that write
// return. Built with AddressSanitizer: a worker that went on to touch the
// destroyed object stops the test with a use after free; and any ALSA call
// after that write returned is counted, sanitizer or not.
#include "systemsounds.h"

#include <QCoreApplication>
#include <alsa/asoundlib.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <mutex>
#include <thread>

using namespace std::chrono_literals;

namespace fake {
std::mutex lock;
std::condition_variable changed;
bool holdNext = false;   // the next write waits for `release`
bool held = false;       // a write is held
bool release = false;
std::atomic<bool> returned{false};     // the held write has returned
std::atomic<int> callsAfterReturn{0};  // ALSA calls made after that
std::atomic<int> writes{0};
char device;

void note()
{
    if (returned)
        ++callsAfterReturn;
}
} // namespace fake

extern "C" {
int snd_pcm_open(snd_pcm_t **pcm, const char *, snd_pcm_stream_t, int)
{
    fake::note();
    *pcm = reinterpret_cast<snd_pcm_t *>(&fake::device);
    return 0;
}

int snd_pcm_set_params(snd_pcm_t *, snd_pcm_format_t, snd_pcm_access_t, unsigned int, unsigned int, int, unsigned int)
{
    fake::note();
    return 0;
}

snd_pcm_sframes_t snd_pcm_writei(snd_pcm_t *, const void *, snd_pcm_uframes_t size)
{
    fake::note();
    std::unique_lock guard(fake::lock);
    ++fake::writes;
    if (fake::holdNext) {
        fake::holdNext = false;
        fake::held = true;
        fake::changed.notify_all();
        fake::changed.wait(guard, [] { return fake::release; });
        fake::returned = true;
        return snd_pcm_sframes_t(size);
    }
    guard.unlock();
    std::this_thread::sleep_for(2ms);  // a device that plays: the write paces the worker
    return snd_pcm_sframes_t(size);
}

int snd_pcm_recover(snd_pcm_t *, int error, int)
{
    fake::note();
    return error;
}

int snd_pcm_drop(snd_pcm_t *)
{
    fake::note();
    return 0;
}

int snd_pcm_delay(snd_pcm_t *, snd_pcm_sframes_t *held)
{
    fake::note();
    *held = 0;
    return 0;
}

int snd_pcm_close(snd_pcm_t *)
{
    fake::note();
    return 0;
}

const char *snd_strerror(int)
{
    return "stand-in error";
}

int snd_lib_error_set_handler(snd_lib_error_handler_t)
{
    return 0;
}
}

namespace {
int passed = 0;
int failed = 0;

void expect(bool ok, const char *what)
{
    std::printf("%s: %s\n", ok ? "ok" : "FAILED", what);
    ok ? ++passed : ++failed;
}

template <typename Ready>
bool waitFor(Ready ready, std::chrono::milliseconds limit)
{
    const auto until = std::chrono::steady_clock::now() + limit;
    while (!ready()) {
        if (std::chrono::steady_clock::now() >= until)
            return false;
        std::this_thread::sleep_for(5ms);
    }
    return true;
}

double secondsSince(std::chrono::steady_clock::time_point start)
{
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
}
} // namespace

int main(int argc, char **argv)
{
    // The sounds are compiled into the shell, not into this test: their
    // absence is said for each, and is not what is checked here.
    qInstallMessageHandler([](QtMsgType, const QMessageLogContext &, const QString &) {});
    QCoreApplication app(argc, argv);

    {
        // An exit with a device that plays: the worker ends and is waited for.
        auto *sounds = new SystemSounds(nullptr);
        sounds->setAwake(true);
        expect(waitFor([] { return fake::writes >= 3; }, 5s), "awake, the worker writes to the device");
        const auto start = std::chrono::steady_clock::now();
        delete sounds;
        expect(secondsSince(start) < 1.0, "the shell leaves at once, with its worker");
    }

    {
        // A write that does not return while the shell is there, and
        // returns once its object is gone.
        fake::writes = 0;
        auto *sounds = new SystemSounds(nullptr);
        sounds->setAwake(true);
        expect(waitFor([] { return fake::writes >= 1; }, 5s), "awake again, the worker writes");
        {
            std::lock_guard guard(fake::lock);
            fake::holdNext = true;
        }
        expect(waitFor([] { std::lock_guard guard(fake::lock); return fake::held; }, 5s),
               "a write is held, as a device that never drains holds it");
        const auto start = std::chrono::steady_clock::now();
        delete sounds;
        const double waited = secondsSince(start);
        expect(waited > 1.5 && waited < 4.0, "the shell waits a moment for the worker, then leaves without it");
        {
            std::lock_guard guard(fake::lock);
            fake::release = true;
        }
        fake::changed.notify_all();
        expect(waitFor([] { return fake::returned.load(); }, 2s), "the held write returns after the object is gone");
        std::this_thread::sleep_for(500ms);  // time for a worker that went on to show it
        expect(fake::callsAfterReturn == 0, "after that write the worker calls nothing more and touches nothing");
    }

    std::printf("sound worker's exit: %d passed, %d failed\n", passed, failed);
    return failed ? 1 : 0;
}
