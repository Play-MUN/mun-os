#include "gamepads.h"

#include "framewatch.h"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QGuiApplication>
#include <QKeyEvent>
#include <QSocketNotifier>
#include <QWindow>

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <linux/input.h>
#include <sys/ioctl.h>
#include <unistd.h>

namespace {

const QString kInputDir = QStringLiteral("/dev/input");
// How long after /dev/input changes the nodes are looked at again, for
// udev to have given the input group access to a new one.
constexpr int kRescanMs = 1000;

// Milliseconds on CLOCK_MONOTONIC: the clock each pad's events are stamped
// with (EVIOCSCLOCKID below), and FrameWatch's.
long long monotonicMs()
{
    timespec now{};
    clock_gettime(CLOCK_MONOTONIC, &now);
    return now.tv_sec * 1000LL + now.tv_nsec / 1000000;
}

bool hasBit(const unsigned long *bits, int bit)
{
    constexpr int perLong = int(sizeof(unsigned long)) * 8;
    return (bits[bit / perLong] >> (bit % perLong)) & 1UL;
}

int keyOf(gamepad::Action action)
{
    switch (action) {
    case gamepad::Action::Up:
        return Qt::Key_Up;
    case gamepad::Action::Down:
        return Qt::Key_Down;
    case gamepad::Action::Left:
        return Qt::Key_Left;
    case gamepad::Action::Right:
        return Qt::Key_Right;
    case gamepad::Action::Accept:
        return Qt::Key_Return;
    case gamepad::Action::Back:
        return Qt::Key_Escape;
    }
    return Qt::Key_unknown;
}

} // namespace

Gamepads::Gamepads(FrameWatch *watch, QObject *parent) : QObject(parent), m_watch(watch)
{
    m_rescan.setSingleShot(true);
    m_rescan.setInterval(kRescanMs);
    connect(&m_rescan, &QTimer::timeout, this, &Gamepads::scan);
    m_repeat.setSingleShot(true);
    m_repeat.setTimerType(Qt::PreciseTimer);
    connect(&m_repeat, &QTimer::timeout, this, &Gamepads::repeat);
    // No /dev/input (a test's container): no pads, and nothing to watch.
    if (!QDir(kInputDir).exists())
        return;
    m_devices.addPath(kInputDir);
    connect(&m_devices, &QFileSystemWatcher::directoryChanged, this, [this] {
        scan();
        m_rescan.start();
    });
    scan();
    m_started = true;
}

Gamepads::~Gamepads()
{
    for (const auto &pad : m_pads) {
        delete pad->notifier;
        ::close(pad->fd);
    }
}

void Gamepads::scan()
{
    const QStringList nodes = QDir(kInputDir).entryList({QStringLiteral("event*")}, QDir::System);
    QSet<QString> present;
    for (const QString &node : nodes)
        present.insert(kInputDir + QLatin1Char('/') + node);
    // A node number freed by a device that left may come back as a pad.
    m_notPads.intersect(present);
    for (const QString &path : present) {
        const bool open = std::any_of(m_pads.cbegin(), m_pads.cend(), [&](const auto &pad) { return pad->path == path; });
        if (!open && !m_notPads.contains(path))
            this->open(path);
    }
}

void Gamepads::open(const QString &path)
{
    const int fd = ::open(QFile::encodeName(path).constData(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0)
        return;  // not readable yet (udev) or gone: the next scan tries again
    unsigned long keys[KEY_CNT / (8 * sizeof(unsigned long)) + 1] = {};
    if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(keys)), keys) < 0 || !hasBit(keys, BTN_SOUTH)) {
        ::close(fd);
        m_notPads.insert(path);
        return;
    }
    // Events stamped on the monotonic clock, comparable with the frames'.
    int clock = CLOCK_MONOTONIC;
    ioctl(fd, EVIOCSCLOCKID, &clock);

    auto pad = std::make_unique<Pad>();
    pad->fd = fd;
    pad->path = path;
    char name[128] = {};
    ioctl(fd, EVIOCGNAME(sizeof(name) - 1), name);
    pad->name = QString::fromUtf8(name);
    for (const int code : {ABS_X, ABS_Y}) {
        input_absinfo info{};
        if (ioctl(fd, EVIOCGABS(code), &info) == 0)
            pad->mapper.setRange(code, {info.minimum, info.maximum});
    }
    input_id id{};
    ioctl(fd, EVIOCGID, &id);
    // Nintendo's pads (and others that present themselves as one) keep
    // Nintendo's layout: A on the right accepts.
    constexpr quint16 kNintendo = 0x057e;
    pad->mapper.setNintendo(id.vendor == kNintendo);
    qInfo("mun-shell: gamepad %s connected (%04x:%04x, %s)", qPrintable(pad->name), id.vendor, id.product,
          qPrintable(path));

    pad->notifier = new QSocketNotifier(fd, QSocketNotifier::Read);
    Pad *raw = pad.get();
    connect(pad->notifier, &QSocketNotifier::activated, this, [this, raw] { read(raw); });
    m_pads.push_back(std::move(pad));
    if (m_started)
        emit connected(raw->name);
}

void Gamepads::read(Pad *pad)
{
    input_event events[64];
    for (;;) {
        const ssize_t n = ::read(pad->fd, events, sizeof(events));
        if (n < 0 && errno == EINTR)
            continue;
        if (n < 0 && errno == EAGAIN)
            break;
        if (n <= 0) {
            close(pad, n == 0 ? "its node ended" : std::strerror(errno));
            return;
        }
        for (ssize_t i = 0; i < n / ssize_t(sizeof(input_event)); ++i) {
            const input_event &e = events[i];
            const long long ms = e.input_event_sec * 1000LL + e.input_event_usec / 1000;
            const std::vector<gamepad::Event> out = pad->mapper.feed(e.type, e.code, e.value, ms);
            if (m_watch && std::any_of(out.cbegin(), out.cend(), [](const gamepad::Event &o) { return o.pressed; }))
                m_watch->inputAt(ms);
            deliver(out);
        }
    }
    schedule();
}

void Gamepads::close(Pad *pad, const char *why)
{
    deliver(pad->mapper.releaseAll());
    qInfo("mun-shell: gamepad %s disconnected (%s)", qPrintable(pad->name), why);
    const QString name = pad->name;
    delete pad->notifier;
    ::close(pad->fd);
    m_pads.erase(std::find_if(m_pads.begin(), m_pads.end(), [pad](const auto &p) { return p.get() == pad; }));
    schedule();
    emit disconnected(name);
}

void Gamepads::repeat()
{
    const long long now = monotonicMs();
    for (const auto &pad : m_pads)
        deliver(pad->mapper.tick(now));
    schedule();
}

void Gamepads::schedule()
{
    long long due = -1;
    for (const auto &pad : m_pads) {
        const long long next = pad->mapper.nextDue();
        if (next >= 0 && (due < 0 || next < due))
            due = next;
    }
    if (due < 0) {
        m_repeat.stop();
        return;
    }
    m_repeat.start(int(std::max(0LL, due - monotonicMs())));
}

void Gamepads::deliver(const std::vector<gamepad::Event> &events)
{
    if (events.empty())
        return;
    QWindow *window = QGuiApplication::focusWindow();
    if (!window && !QGuiApplication::topLevelWindows().isEmpty())
        window = QGuiApplication::topLevelWindows().constFirst();
    if (!window)
        return;
    for (const gamepad::Event &e : events) {
        QCoreApplication::postEvent(window, new QKeyEvent(e.pressed ? QEvent::KeyPress : QEvent::KeyRelease,
                                                          keyOf(e.action), Qt::NoModifier, QString(), e.repeat));
    }
}
