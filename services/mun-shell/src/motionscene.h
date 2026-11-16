// MotionScene plays a MUN Shape package's motion (mun-shape/1.1, draft;
// docs/shape-motion.md) behind Home: planes, actors with frame animations
// on paths, particles and lights, the beats of activity and the reactions
// to the console's events. The package's data, normalised by the card
// service's checker (Shape.motion), says everything; nothing from the card
// runs.
//
// Actors travel smooth curves at a speed that eases at the ends, pulses with
// their strokes and fades in their glides; they turn to face where they go
// (through a turn clip, or a quick turn about their middle), pitch with
// their course, and plants bend from their foot in bands, leaning with the
// water's current, which also carries the particles.
//
// The simulation runs in fixed steps of 1/rate s; drawing follows the
// display's frames (Qt's animation driver), what moves interpolated between
// the last two steps.
//
// One simulation, two ways of drawing what it decides (`backend`, from
// MUN_SHELL_MOTION_BACKEND, "graph" by default):
// - "cpu": a worker thread composes each frame into an image at the look's
//   resolution (640 x 360 for the pixel look), shown enlarged; the GUI
//   thread never paints and never waits for it (a tick that finds it busy
//   is skipped).
// - "graph": one scene graph node per thing drawn, updated each tick; the
//   GPU draws them on a board, Qt Quick's software renderer in the virtual
//   console. The pixel look is kept by snapping places to whole scene
//   pixels and sampling the images without smoothing.
// Each tick produces one list of things to draw (DrawItem) and both
// backends draw that same list, so they show equivalent content: what they
// cost is what this draft is to measure. Blending is plain alpha in both
// for now.
//
// Images are decoded once per package on a thread of their own, never on
// the GUI thread; until they are, nothing is drawn (`drawn` false). If one
// cannot be decoded, if together they are over their budget (64 MiB at 4
// bytes a pixel, the checker's: mun_card.motion.MOTION_MAX_DECODED), if
// the export is gone, or if the motion declares nothing to draw (no plane,
// actor, emitter or light), nothing is drawn and `failed` says so, with the
// cause in the journal: Home then draws the package's v1 world instead.
//
// The package comes whole (`source`: its motion, its export's root and its
// insertion), so a load never mixes two packages. A console package's
// export must be its insertion's own (the card service names it
// <insertion>.<attempt>; see kExportName) and, before any image is decoded,
// the marker `shape-decoding-motion` in the runtime directory must be
// written naming the insertion; it is removed when decoding ends, decoded
// or not. If decoding takes the shell down, the next start skips that
// insertion's motion (Shape) and shows its v1 look; a clean stop removes the
// marker (ExecStopPost in deploy/mun-shell.service). If the export is
// another's or the marker cannot be written, nothing is decoded and
// `failed` says so. A package without an insertion (the laboratory's
// scenes) is loaded without a marker.
// Laboratory output: the log says the backend, the images and, every 10
// seconds, ticks, frames composed, ticks skipped and composition times.
//
// GUI thread only, except the loader and the composer threads, which only
// read the images they were given and post their results back.
#pragma once

#include <QAbstractAnimation>
#include <QElapsedTimer>
#include <QImage>
#include <QPointF>
#include <QQuickItem>
#include <QRectF>
#include <QString>
#include <QTimer>
#include <QVariantMap>
#include <QVector3D>

#include "motionclock.h"

#include <atomic>
#include <cmath>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <random>
#include <thread>
#include <vector>

namespace motion {

// One thing to draw this frame, in canvas pixels (1920 x 1080), far first.
struct DrawItem {
    int image = -1;          // index into the decoded images
    QRectF source;           // the part of the image (a frame of a sheet)
    QRectF target;           // where, in canvas pixels
    qreal opacity = 1;
    bool mirror = false;
    qreal rotation = 0;      // degrees, clockwise, about the target's centre
};

struct Clip {
    int first = 0, count = 1;
    qreal fps = 8;
    bool once = false;
    bool blend = false;      // loops, each frame dissolving into the next
};

struct Sheet {
    int image = -1;
    QSize frame;
    int columns = 1;
    int views = 1;           // rows of headings; clips then name columns
    QHash<QString, Clip> clips;
};

struct Curve {
    qreal min = 0, max = 0, period = 1, phase = 0;
    QString shape = QStringLiteral("sine");
    qreal at(qreal t, qreal phase) const;
};

struct Vary {
    QHash<QString, Curve> curves;
    qreal value(const QString &property, qreal t, qreal phase, qreal otherwise) const;
};

struct Path {
    std::vector<QPointF> points;
    QString from = QStringLiteral("here"), to, ease = QStringLiteral("inout");
    bool around = false, loop = false;
    QPointF radius;
    qreal turns = 1, seconds = -1, speed = 120;
};

struct State {
    QString kind, path, clip, then;
    Vary vary;
    bool hidden = false;
    bool onlyVisible = false;
    qreal seconds = -1;      // a state without a path lasts this long, then `then`
    // swimming: speed pulsing with each cycle of the clip, and glides
    qreal surge = 0;
    qreal surgeAt = 0.25;    // where in the clip's cycle it is fastest (0..1)
    QString glideClip;
    QPoint glideEvery;       // cycles of the clip between two glides
    QPointF glideSeconds;
    // emitters and lights
    bool on = true;
    int burst = 0;
    qreal rate = 1, opacity = -1;
};

struct Actor {
    QString name, sheet, clip, rest = QStringLiteral("idle"), keep;
    QPointF at;
    qreal z = 0.5;           // its depth at rest, where its images have their own size
    qreal zNow = 0.5, prevZ = 0.5, vz = 0;   // its depth now, a step before, and how fast it changes
    bool views = false;      // face "views": shows the row of the heading it moves in
    qreal yaw = 0;           // radians: 0 going right, pi/2 going away (smoothed)
    int view = 0;
    bool mirror = false;   // face "mirror": it turns to face where it goes
    qreal bank = 0;        // how far it pitches with its course (0..1)
    QString turnClip;      // played once to turn around (mirrored the other way)
    bool bends = false;    // bends from its base (plants)
    qreal bendAmount = 0, bendPeriod = 5, bendWave = 0.4, bendFollow = 0.5;
    int copies = 1;
    QPointF spread;
    qreal lag = 0;
    QPointF wobble;          // the copies' sway up and down: pixels, seconds
    QStringList copyClips;   // shown by the copies in turn
    QString avoid;           // the actor it keeps away from
    qreal avoidRadius = 0, avoidPush = 0;
    QVector3D shift, prevShift;   // keeping away: pixels sideways and up, depth
    QVector3D join;          // joining a loop: where it is less where the loop is, fading
    // running
    QString state;           // the current state's label (rest when calm)
    QString clipNow;
    qreal clipSince = 0;
    QPointF pos, velocity;   // canvas pixels, and pixels a second (smoothed)
    QPointF prevPos;         // where it was a step before, for drawing in between
    int facing = 1;          // 1 as drawn, -1 mirrored
    bool turning = false;
    bool turnPending = false;   // wanted, waiting for the stroke's first pose
    qreal turnWantedSince = 0;
    qreal lastTurnEnd = -1e9;
    int turnTo = 1;
    qreal turnSince = 0;
    qreal pitch = 0;         // degrees, smoothed
    bool hidden = false;
    bool reacting = false;   // in a focus reaction: holds at its path's end
    bool returning = false;
    // the course: a polyline (a smooth curve sampled), travelled at a speed
    // that eases at the ends, pulses with the strokes and fades in glides
    std::vector<QVector3D> course;  // x, y canvas pixels, z depth
    std::vector<qreal> courseAt;   // distance along it at each point (depth counted by perspective)
    qreal courseLength = 0, courseDone = 0, courseSpeed = 0, courseStart = 0;
    QString courseEase, after;
    bool courseLoop = false;
    qreal speedScale = 1;    // eased: 1 swimming, lower while gliding
    qreal stateUntil = -1;   // a timed state's end
    // the swim's rhythm
    int strokesLeft = 0;
    int lastCycle = 0;
    bool gliding = false;
    qreal glideUntil = 0;
    std::vector<QVector3D> offsets; // the copies' places in formation (pixels at its depth, depth)
};

struct Particle {
    QPointF pos, velocity;
    QPointF prev;            // where it was a step before, for drawing in between
    qreal age = 0, life = 1, phase = 0;
};

struct Emitter {
    QString name, sheet, clip, keep;
    enum Source { Region, Ring, Point } source = Region;
    QRectF region;
    QPointF point;
    qreal ringInner = 0, ringOuter = 0;
    bool towardDisc = false;
    qreal swirl = 0, rate = 0, spread = 0, gravity = 0, sway = 0, grow = 0, fade = 0, z = 0.5;
    bool pull = false;       // the disc draws in what comes near
    qreal pullRadius = 0, pullSpeed = 0, pullSwirl = 0, pullSwallow = 0;
    QPointF lifeRange, velocity;
    int burst = 0, max = 16;
    bool on = true;
    qreal rateFactor = 1, accumulated = 0;
    std::vector<Particle> particles;
};

struct Light {
    QString name, keep;
    int image = -1;
    QPointF at;
    bool atDisc = false, hasAt = false;
    qreal radius = 220, z = 0.5, opacity = 1, base = 1;   // opacity: now; base: its own
    QPointF drift;
    Vary vary;
    bool on = true;
    qreal target = -1;
};

struct Plane {
    int image = -1;
    qreal z = 0.5;
    QPointF at;              // its top left corner
    QPointF drift;
    Vary vary;
};

} // namespace motion

class MotionScene : public QQuickItem {
    Q_OBJECT
    QML_ELEMENT
    // {motion, root, insertion}: the package's motion (Shape.motion), its
    // export's directory and the insertion it is of, set together.
    Q_PROPERTY(QVariantMap source READ source WRITE setSource NOTIFY motionChanged)
    // Where the card object is, in canvas pixels: the anchor `disc`.
    Q_PROPERTY(QPointF disc READ disc WRITE setDisc NOTIFY discChanged)
    Q_PROPERTY(bool running READ running WRITE setRunning NOTIFY runningChanged)
    // Reduce motion: the poster, or one still frame, and no motion.
    Q_PROPERTY(bool still READ still WRITE setStill NOTIFY runningChanged)
    Q_PROPERTY(QString backend READ backend CONSTANT)
    Q_PROPERTY(bool drawn READ drawn NOTIFY drawnChanged)
    // The motion could not be loaded (see above): nothing of it is drawn.
    Q_PROPERTY(bool failed READ failed NOTIFY drawnChanged)
    // For tests and the laboratory, read when asked (no change signal): the
    // things the last tick listed to draw, and the ticks since the package
    // was loaded.
    Q_PROPERTY(int things READ things)
    Q_PROPERTY(int ticks READ ticks)
    // the simulation's steps since the package was loaded (ticks are frames)
    Q_PROPERTY(int steps READ steps)
    // Laboratory: on the graphics path, the nodes the things were drawn in
    // (runs from one image); 0 where each thing is a node of its own.
    Q_PROPERTY(int runs READ runs)

public:
    explicit MotionScene(QQuickItem *parent = nullptr);
    ~MotionScene() override;

    QVariantMap source() const;
    void setSource(const QVariantMap &source);
    QPointF disc() const { return m_disc; }
    void setDisc(QPointF disc);
    bool running() const { return m_running; }
    void setRunning(bool running);
    bool still() const { return m_still; }
    void setStill(bool still);
    QString backend() const { return m_backend; }
    bool drawn() const { return m_drawn; }
    bool failed() const { return m_failed; }
    int things() const { return int(m_items.size()); }
    int ticks() const { return m_totalTicks; }
    int steps() const { return int(std::lround(m_simT * m_rate)); }
    int runs() const { return m_runs; }

    // One of the console's events: "arrive", "focus.play", "focus.away",
    // "launch", "return", "leave"; and "launch.refused", the console's own
    // (no package reacts to it): Play was pressed and the game did not start
    // (the launcher refused, or its preparation failed after it accepted;
    // LaunchClient::launchFailed), so what the launch reaction changed goes
    // back, and Play's focus
    // reaction with it if Play is still in focus. A launch is taken once
    // until it is refused or the identity comes in again. At most one of each
    // waits for the next step (the focus: its latest); held still (Reduce
    // motion), only the focus waits, for when the scene moves again.
    Q_INVOKABLE void event(const QString &name);
    // For tests and the laboratory: where an actor is now and in what state
    // ({x, y, z, state, hidden}; empty if there is none of that name); and
    // an event applied at a given step (the next, if that one has passed),
    // so that two runs apply the same events at the same steps whatever
    // their frames.
    Q_INVOKABLE QVariantMap actorAt(const QString &name) const;
    Q_INVOKABLE void eventAt(const QString &name, int step);

signals:
    void motionChanged();
    void discChanged();
    void runningChanged();
    void drawnChanged();

protected:
    QSGNode *updatePaintNode(QSGNode *old, UpdatePaintNodeData *data) override;

private:
    QSGNode *updateBatches(QSGNode *old);
    void trace();

private:
    void reload();
    void loaded(quint64 generation, std::shared_ptr<std::vector<QImage>> images, const QString &failure);
    void fail(const QString &why);
    void build();
    void tick();
    void simulate(qreal t, qreal dt);
    // What to draw at time t, places taken `alpha` (0..1) of the way from
    // the step before to the last one.
    void collect(qreal t, qreal alpha = 1);
    void onFrame();
    void startState(motion::Actor &actor, const QString &label, qreal t, bool reaction);
    void goHome(motion::Actor &actor, qreal t);
    void setCourse(motion::Actor &actor, std::vector<QVector3D> points, qreal seconds, qreal speed,
                   const QString &ease, bool loop, bool smooth, const QString &after, qreal t, bool even);
    void keepAway(motion::Actor &actor, qreal dt);
    qreal depthScale(qreal z) const;
    QPointF swayAt(qreal t, qreal z) const;
    void moveActor(motion::Actor &actor, qreal t, qreal dt);
    void switchClip(motion::Actor &actor, const QString &clip, qreal t);
    int frameOf(const motion::Actor &actor, const motion::Clip &clip, qreal t, int copy) const;
    // A clip as the actor shows it now: its "-left" version, drawn as is,
    // while it faces left and its sheet has one.
    QString shownClip(const motion::Actor &actor, const QString &clip, int facing) const;
    QPointF currentAt(qreal t) const;
    void applyEvent(const QString &name, qreal t);
    void applyTargets(const QVariantMap &moves, qreal t, bool reaction);
    // What `moves` changed back to its own: actors home, emitters and
    // lights to their own state.
    void restore(const QVariantMap &moves, qreal t);
    void runBeat(qreal t);
    void composerLoop();
    void composed(QImage frame, qint64 nanoseconds);
    void report();

    QVariantMap m_motion;
    QString m_root, m_backend;
    QPointF m_disc{470, 540};
    bool m_running = false, m_still = false, m_drawn = false, m_failed = false;
    QString m_insertion;

    // the package, as built from m_motion and the decoded images
    std::shared_ptr<std::vector<QImage>> m_images;
    QHash<QString, int> m_imageIndex;
    QHash<QString, motion::Sheet> m_sheets;
    QHash<QString, motion::State> m_states;
    std::vector<motion::Plane> m_planes;
    std::vector<motion::Actor> m_actors;
    std::vector<motion::Emitter> m_emitters;
    std::vector<motion::Light> m_lights;
    QSize m_resolution;            // composition (the look's), canvas size for native
    bool m_nearest = false;
    qreal m_k = 1;                 // canvas pixels per scene pixel
    int m_rate = 20;
    QVariantMap m_react;
    QVariantList m_beats;
    QPointF m_beatGap{6, 18};
    QPointF m_current;             // the water's current, canvas pixels a second
    bool m_hasDepth = false;       // perspective by depth (scene.depth)
    qreal m_depthFar = 1, m_depthSpan = 2000;
    bool m_hasSway = false;        // the viewpoint drifting (scene.sway)
    QPointF m_swayAmount, m_swayPeriod{56, 69};
    qreal m_swayPivot = 0.5;
    qreal m_gust = 0, m_gustPeriod = 12;
    qreal m_nextBeat = 0;
    QHash<int, qreal> m_beatLast;
    QString m_poster;

    // focus reactions
    bool m_focused = false;
    qreal m_focusSince = 0;
    bool m_focusFired = false;
    qreal m_focusLast = -1e9;
    // Play pressed, the game not started yet; and whether Play's focus
    // reaction had run then (it runs again if the launch is refused).
    bool m_launched = false, m_launchedFocused = false;
    bool m_tooMany = false;  // said once: the list of things over its bound

    std::mt19937 m_random{1969};
    QElapsedTimer m_clock;
    MotionClock m_stepper{1.0 / 20};   // its fixed steps (src/motionclock.h)
    // The simulation advances in fixed steps of 1/rate, and the console's
    // events are applied at the next step, so the same events at the same
    // steps give the same scene on any machine (the seed is fixed).
    qreal m_simT = 0;
    QStringList m_pending;
    std::vector<std::pair<int, QString>> m_scheduled;   // eventAt
    int m_lateSteps = 0;     // steps skipped when the console fell behind
    // Drawing follows the display's frames: an animation of Qt's (no end),
    // advanced by the window's animation driver once a frame (vsync on a
    // console), which also keeps frames coming while the scene runs.
    QAbstractAnimation *m_driver = nullptr;
    int m_frameTicks = 0, m_paints = 0;   // laboratory counts, every 10 s
    QTimer m_timer;
    quint64 m_generation = 0;
    std::thread m_loader;

    // this tick's list, for the graph backend (read in updatePaintNode, with
    // the GUI thread blocked)
    std::vector<motion::DrawItem> m_items;
    bool m_itemsDirty = false;

    // the CPU backend
    std::thread m_composer;
    std::mutex m_mutex;
    std::condition_variable m_wake;
    bool m_stop = false;
    bool m_job = false;
    std::vector<motion::DrawItem> m_jobItems;
    std::atomic<bool> m_busy{false};
    QImage m_frame;
    bool m_frameDirty = false;

    // laboratory counts, every 10 s
    QElapsedTimer m_since;
    int m_ticks = 0, m_composedCount = 0, m_skipped = 0;
    int m_totalTicks = 0;
    int m_runs = 0;          // written on the render thread while the GUI thread waits (sync)
    bool m_trace = false;    // MUN_SHELL_MOTION_TRACE: a digest of the scene each second (laboratory)
    std::vector<qint64> m_composeNs;
};
