// FrameWatch keeps navigation first while a Game Card's world is drawn
// (docs/shape.md, "Objectives"): it times the window's frames and the time
// from a key to the next frame on screen, and when navigation suffers says
// so (`strained`), for the world to step down one level of detail.
//
// Navigation is what it protects: a frame interval counts only for kActive
// after a key (the entries and the panel move); a world's own frames at 10
// or 20 per second are not missed frames of the interface. Every kWindow it
// looks at what it gathered: with at least kMinFrames intervals, a 95th
// percentile over kFrameLimit, or a median key-to-frame time over kKeyLimit,
// is strain, and strain in kStrainedWindows windows in a row is said (one
// heavy moment, such as a large panel changing, is MUN's own and passes).
// After a step down it waits kCalm before judging again (hysteresis). A Shape transition (`animating`) is short and bounded: its
// frames are timed for the laboratory, not judged, so one transition never
// lowers a world for the rest of its insertion. The limits are the
// proposal's hypotheses, measured in the laboratory.
//
// With MUN_SHELL_TIMING set to a file path (laboratory only), every key's
// latency, and each window's frame statistics, are appended to that file.
//
// GUI thread only: the window's frameSwapped arrives there with the basic
// render loop the shell uses.
#pragma once

#include <QElapsedTimer>
#include <QFile>
#include <QObject>
#include <QPointer>
#include <QQuickWindow>
#include <QTimer>
#include <QtQml/qqmlregistration.h>

#include <vector>

class FrameWatch : public QObject {
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON
    // The window to watch, and whether strain is to be judged at all (a
    // world is drawn).
    Q_PROPERTY(QQuickWindow *window READ window WRITE setWindow NOTIFY windowChanged)
    Q_PROPERTY(bool judging READ judging WRITE setJudging NOTIFY judgingChanged)
    Q_PROPERTY(bool animating READ animating WRITE setAnimating NOTIFY animatingChanged)

public:
    static constexpr qint64 kIdleMs = 100;
    static constexpr qint64 kActiveMs = 600;
    static constexpr int kWindowMs = 2000;
    static constexpr int kMinFrames = 20;
    static constexpr qreal kFrameLimitMs = 25;
    static constexpr qreal kKeyLimitMs = 50;
    static constexpr int kCalmMs = 6000;
    static constexpr int kStrainedWindows = 2;

    explicit FrameWatch(QObject *parent = nullptr);

    // A key was pressed now: its latency ends at the next frame on screen.
    Q_INVOKABLE void keyPressed();
    // Writes a line to the laboratory's log (if any): a mark in the timings.
    Q_INVOKABLE void mark(const QString &what);

    QQuickWindow *window() const { return m_window; }
    void setWindow(QQuickWindow *window);
    bool judging() const { return m_judging; }
    void setJudging(bool judging);
    bool animating() const { return m_animating; }
    void setAnimating(bool animating);

signals:
    void windowChanged();
    void judgingChanged();
    void animatingChanged();
    void strained(const QString &why);

private:
    void onFrame();
    void judge();
    void log(const QString &line);

    QPointer<QQuickWindow> m_window;
    bool m_judging = false;
    bool m_animating = false;
    qint64 m_activeUntil = -1;
    QElapsedTimer m_clock;
    qint64 m_lastFrame = -1;
    qint64 m_keyAt = -1;
    qint64 m_calmUntil = 0;
    int m_strained = 0;   // windows in a row with strain
    std::vector<qreal> m_intervals;     // while navigating: judged
    std::vector<qreal> m_transition;    // during a transition: logged
    std::vector<qreal> m_keys;
    QTimer m_windowTimer;
    QFile m_log;
};
