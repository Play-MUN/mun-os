// SceneClock paces the shell's painted animation layers (Backdrop, HeroIcon)
// with one timer, so layers that move in the same frame are painted for one
// scene-graph frame instead of several. Each layer states the rate it needs
// while it has motion to show and paints on the ticks of that rate; with no
// demand the timer stops, and a still screen costs nothing.
//
// Rates are 60, 20, 10 and 5 frames per second, each a multiple of the next,
// so every layer's frames fall on the ticks of the fastest one. Pace, below,
// eases a layer's motion to a stop and back.
//
// GUI thread only: the layers are Qt Quick items, and the ticks are delivered
// on the thread that created the clock.
#pragma once

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

signals:
    void tick(quint64 frame);

private:
    SceneClock();
    void retime();

    QTimer m_timer;
    QHash<const QObject *, int> m_demands;
    quint64 m_frame = 0;
    int m_step = 1;   // base ticks per timer tick
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
