// Gamepads for the menus: every pad Linux's input layer reports (a
// /dev/input/event* node with a gamepad's south button) is read here and
// delivered to the window as the keys a keyboard gives for the same thing
// (Up, Down, Left, Right, Return, Escape), pressed, repeated and released,
// so the menus treat both alike: the first input wakes the interface, the
// key that woke it is spent until let go, every move sounds (qml/Main.qml).
// The rules are gamepadmap.h's.
//
// Not a virtual keyboard: nothing goes back through the kernel. An event read
// here becomes a key posted to the window in the same process and handled on
// the next turn of the event loop, as the keyboard's are; the time from the
// kernel's event to the frame that shows it is logged for the laboratory
// (FrameWatch::inputAt). Pads come and go: /dev/input is watched, a new node
// is tried again shortly after it appears (udev gives the shell's input group
// access a moment later), and a pad whose node fails (disconnected) is
// closed, what it held let go. Nodes that are not pads are not opened again
// while they exist.
//
// Threads: the GUI thread's.
#pragma once

#include <QFileSystemWatcher>
#include <QObject>
#include <QPointer>
#include <QSet>
#include <QString>
#include <QTimer>

#include <memory>
#include <vector>

#include "gamepadmap.h"

class FrameWatch;
class QSocketNotifier;

class Gamepads : public QObject {
    Q_OBJECT

public:
    // `watch` (may be null) is told each input's kernel time.
    explicit Gamepads(FrameWatch *watch, QObject *parent = nullptr);
    ~Gamepads() override;

private:
    struct Pad {
        int fd = -1;
        QString path;
        QString name;
        QSocketNotifier *notifier = nullptr;
        gamepad::Mapper mapper;
    };

    void scan();
    void open(const QString &path);
    void read(Pad *pad);
    void close(Pad *pad, const char *why);
    void repeat();
    void deliver(const std::vector<gamepad::Event> &events);
    void schedule();

    QPointer<FrameWatch> m_watch;
    QFileSystemWatcher m_devices;
    QTimer m_rescan;
    QTimer m_repeat;
    std::vector<std::unique_ptr<Pad>> m_pads;
    QSet<QString> m_notPads;
};
