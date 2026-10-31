// SceneClock paces the shell's animation layers (Backdrop, HeroIcon) with one
// clock, so layers that move in the same frame are drawn for one scene-graph
// frame instead of several. Each layer states the rate it needs while it has
// motion to show and draws on the ticks of that rate; with no demand the
// clock stops, and a still screen costs nothing.
//
// Rates are 60, 20, 10 and 5 frames per second, each a multiple of the next,
// so every layer's frames fall on the ticks of the fastest one. The ticks
// come from one of two sources:
// - A timer (the default, and what Qt Quick's software renderer keeps): it
//   ticks at the fastest rate demanded. Its interval is whole milliseconds,
//   not the display's frame.
// - Qt Quick's animation driver, once setSynced(true) (an accelerated
//   renderer, src/renderinfo.h): one tick per frame the window renders,
//   which on the threaded render loop is the display's frame. A layer at 60
//   then moves on every frame, whatever the display's rate (seconds() keeps
//   the speed), and a lower rate on every third, sixth... frame as at 60.
// seconds() is the time of the current tick; layers move by it. Pace, below,
// eases a layer's motion to a stop and back.
//
// GUI thread only: the layers are Qt Quick items, and the ticks are delivered
// on the thread that created the clock (the animation driver advances the
// GUI thread's animations there).
#pragma once

#include <QElapsedTimer>
#include <QHash>
#include <QObject>
#include <QTimer>

#include <algorithm>

class SceneClock : public QObject {
    Q_OBJECT

public:
    static constexpr int kBaseRate = 60;

    // Created on first use, owned by the QCoreApplication (which must exist).
    static SceneClock *instance();

    // `who` needs `rate` frames per second from now on (rounded up to an
    // allowed rate); 0 withdraws its demand. `who` is only a key: the clock
    // never dereferences it, and a layer withdraws its demand before it is
    // destroyed.
    void demand(const QObject *who, int rate);
    // True on the ticks where a layer running at `rate` paints.
    static bool due(quint64 frame, int rate);
    static int allowedRate(int rate);

    // Ticks on the animation driver's frames (true) or on the timer (false,
    // the default). Set once the renderer is known; logged.
    void setSynced(bool synced);
    bool synced() const { return m_synced; }
    // Seconds of the current tick, from an arbitrary start; never decreases.
    qreal seconds() const { return m_seconds; }

signals:
    void tick(quint64 frame);
    // Layers whose rates depend on the source state their demand again.
    void syncedChanged();

private:
    class Ticker;

    SceneClock();
    void retime();
    void advance(qreal seconds);

    QTimer m_timer;
    QElapsedTimer m_sinceTick;   // the timer's: since its last tick or start
    Ticker *m_ticker;
    QHash<const QObject *, int> m_demands;
    quint64 m_frame = 0;
    int m_step = 1;   // base ticks per tick
    bool m_synced = false;
    qreal m_seconds = 0;
};

// The speed of a layer's ongoing motion (a drift, a float, a turning gear),
// from 0 (still) to 1, eased over kSeconds whenever it is told to stop or go
// on: the motion slows to a stop and picks up again, instead of freezing on
// one frame and jumping back into motion on the next key.
class Pace {
public:
    static constexpr qreal kSeconds = 1.5;

    void setMoving(bool moving) { m_target = moving ? 1 : 0; }
    // True once it has stopped and is not about to start.
    bool still() const { return m_value == 0 && m_target == 0; }
    // Advances `dt` seconds and returns how far the motion's own time moves
    // in them.
    qreal advance(qreal dt)
    {
        const qreal before = speed();
        const qreal change = dt / kSeconds;
        m_value = m_target > m_value ? std::min(m_target, m_value + change) : std::max(m_target, m_value - change);
        return dt * (before + speed()) / 2;
    }

private:
    qreal speed() const { return m_value * m_value * (3 - 2 * m_value); }

    qreal m_value = 1;
    qreal m_target = 1;
};
