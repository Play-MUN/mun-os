// ShapeWorld draws a Game Card's MUN Shape world behind Home (docs/shape.md,
// "world"): its backdrop, up to three layers, up to two sprite emitters and
// up to two light textures, and the front of the transition that brings the
// world in or takes it away (shapefront.h). The package describes the world;
// this code is the same for every package.
//
// Navigation first (docs/shape.md, "Resources"): the world is prepared and
// painted on a thread of its own, never on the GUI thread, into two frames
// that take turns. The GUI thread only shows the last finished frame; a
// frame the GUI has not taken yet is never painted over, and a tick that
// finds no free frame is skipped, never waited for. The world moves at the
// package's rate (10 or 20 frames per second); its transitions at
// kTransitionRate. A still world, or one at rest, is painted once and costs
// nothing more.
//
// Preparing: every image is decoded one at a time, its dimensions checked
// from its header first and Qt's allocation limit in force, and scaled once
// to the display's device pixels (the backdrop to cover the canvas, a layer
// to the canvas's height, a light texture over the canvas, a sprite by its
// emitter's scale); only what can show is made (the backdrop's part the
// canvas shows, a layer's band of rows that show, a light texture's part that
// shows), and the decoded source is freed at once. What a world would take at
// its peak is estimated from the headers before anything is decoded (the
// frames and a frame's worth of Qt's copies, the export, what the level keeps,
// and the one image being prepared, decoded and converted), and the richest
// detail level that fits the display's budget is chosen:
//   0 full; 1 without light textures; 2 the backdrop and the nearest layer
//   at 10 frames per second; 3 still: the backdrop and the nearest layer,
//   composed once onto the backdrop; 4 none (the palette alone, over MUN's
//   world).
// Each image is then checked against what the level has left before its
// memory is taken; a level that would go over is given up first, and the next
// one tried. stepDown() lowers the level for the rest of the world (the
// watchdog); it only frees memory. An image that fails to decode drops the
// whole world (docs/shape.md: a block is taken or dropped whole), and
// `failed` says so.
//
// Thread contract: properties on the GUI thread; the painter's thread owns the
// prepared images; the two frames are handed over under a mutex, and a frame
// is written only while the scene graph does not hold it. The thread is
// joined by the destructor.
#pragma once

#include <QColor>
#include <QImage>
#include <QMutex>
#include <QPointF>
#include <QQuickItem>
#include <QSize>
#include <QThread>
#include <QVariantMap>
#include <QtQml/qqmlregistration.h>

#include <memory>

class WorldPainter;

class ShapeWorld : public QQuickItem {
    Q_OBJECT
    QML_ELEMENT

    // The export's normalised `world` block and the export's folder; an
    // empty block means no world.
    Q_PROPERTY(QVariantMap world READ world WRITE setWorld NOTIFY worldChanged)
    Q_PROPERTY(QString root READ root WRITE setRoot NOTIFY worldChanged)
    // The transition shown: "tide", "fade" or "sweep", and how far it is,
    // 0 (MUN) to 1 (the world whole). A world at 1 covers the canvas.
    Q_PROPERTY(QString transition READ transition WRITE setTransition NOTIFY transitionChanged)
    Q_PROPERTY(qreal progress READ progress WRITE setProgress NOTIFY progressChanged)
    Q_PROPERTY(QPointF orb READ orb WRITE setOrb NOTIFY orbChanged)
    // MUN's scrim over the world while Settings or a dialog is shown, 0 to 1,
    // in `scrimColour`.
    Q_PROPERTY(qreal dim READ dim WRITE setDim NOTIFY dimChanged)
    Q_PROPERTY(QColor scrimColour READ scrimColour WRITE setScrimColour NOTIFY dimChanged)
    // Canvas pixels the layers follow navigation by, times their depth.
    Q_PROPERTY(qreal parallax READ parallax WRITE setParallax NOTIFY parallaxChanged)
    // Whether the world moves: false eases it to a stop (rest).
    Q_PROPERTY(bool running READ running WRITE setRunning NOTIFY runningChanged)
    // Reduce motion: the world is composed once and stays still.
    Q_PROPERTY(bool still READ still WRITE setStill NOTIFY stillChanged)
    // The front's light and its inner line.
    Q_PROPERTY(QColor frontLight READ frontLight WRITE setFrontLight NOTIFY frontChanged)
    Q_PROPERTY(QColor frontAccent READ frontAccent WRITE setFrontAccent NOTIFY frontChanged)

    // Prepared for the current world (true at once for no world).
    Q_PROPERTY(bool ready READ ready NOTIFY preparedChanged)
    // A world is prepared and drawn (false for none, at level 4, or failed).
    Q_PROPERTY(bool drawn READ drawn NOTIFY preparedChanged)
    // The world covers the canvas with an opaque frame: what is beneath rests.
    Q_PROPERTY(bool covering READ covering NOTIFY preparedChanged)
    Q_PROPERTY(int detail READ detail NOTIFY preparedChanged)
    Q_PROPERTY(bool failed READ failed NOTIFY preparedChanged)
    // The estimate the level was chosen by, and the budget, in bytes.
    Q_PROPERTY(qint64 estimate READ estimate NOTIFY preparedChanged)
    Q_PROPERTY(qint64 budget READ budget NOTIFY preparedChanged)
    // Frames shown so far (for the laboratory and the tests: a still world
    // shows no new ones).
    Q_PROPERTY(int frames READ frames NOTIFY framesChanged)

public:
    static constexpr int kTransitionRate = 30;

    explicit ShapeWorld(QQuickItem *parent = nullptr);
    ~ShapeWorld() override;

    // The watchdog: one level down for the rest of this world.
    Q_INVOKABLE void stepDown(const QString &why);

    QVariantMap world() const { return m_world; }
    void setWorld(const QVariantMap &world);
    QString root() const { return m_root; }
    void setRoot(const QString &root);
    QString transition() const { return m_transition; }
    void setTransition(const QString &kind);
    qreal progress() const { return m_progress; }
    void setProgress(qreal progress);
    QPointF orb() const { return m_orb; }
    void setOrb(const QPointF &orb);
    qreal dim() const { return m_dim; }
    void setDim(qreal dim);
    QColor scrimColour() const { return m_scrim; }
    void setScrimColour(const QColor &colour);
    qreal parallax() const { return m_parallax; }
    void setParallax(qreal parallax);
    bool running() const { return m_running; }
    void setRunning(bool running);
    bool still() const { return m_still; }
    void setStill(bool still);
    QColor frontLight() const { return m_frontLight; }
    void setFrontLight(const QColor &colour);
    QColor frontAccent() const { return m_frontAccent; }
    void setFrontAccent(const QColor &colour);

    bool ready() const { return m_ready; }
    bool drawn() const { return m_ready && m_detail < 4 && !m_failed; }
    bool covering() const { return drawn() && m_progress >= 1; }
    int detail() const { return m_detail; }
    bool failed() const { return m_failed; }
    qint64 estimate() const { return m_estimate; }
    qint64 budget() const { return m_budget; }
    int frames() const { return m_framesShown; }

    // Shared between the item and its painter.
    struct Params {
        QString kind = QStringLiteral("fade");
        qreal progress = 0;
        QPointF orb{470, 570};
        qreal dim = 0;
        QColor scrim{8, 9, 11};
        qreal parallax = 0;
        bool running = true;
        bool still = false;
        QColor frontLight{230, 250, 255};
        QColor frontAccent{240, 184, 90};
        quint64 version = 0;
    };
    struct Frames {
        QMutex mutex;
        QImage images[2];
        int shown = -1;   // held by the scene graph
        int ready = -1;   // painted, not taken yet
        Params params;
    };

signals:
    void worldChanged();
    void transitionChanged();
    void progressChanged();
    void orbChanged();
    void dimChanged();
    void parallaxChanged();
    void runningChanged();
    void stillChanged();
    void frontChanged();
    void preparedChanged();
    void framesChanged();
    // To the painter's thread.
    void prepareRequested(quint64 generation, const QVariantMap &world, const QString &root, const QSize &device,
                          qreal scale, qint64 budget);
    void detailRequested(quint64 generation, int detail);
    void wakeRequested();

protected:
    QSGNode *updatePaintNode(QSGNode *old, UpdatePaintNodeData *data) override;
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;
    void itemChange(ItemChange change, const ItemChangeData &value) override;

private:
    void prepare();
    void pushParams();
    void onPrepared(quint64 generation, bool ok, int detail, qint64 estimate, const QString &note);
    void onFrame();

    std::shared_ptr<Frames> m_frames;
    QThread m_thread;
    WorldPainter *m_painter = nullptr;
    quint64 m_generation = 0;
    QSize m_device;

    QVariantMap m_world;
    QString m_root;
    QString m_transition = QStringLiteral("fade");
    qreal m_progress = 0;
    QPointF m_orb{470, 570};
    qreal m_dim = 0;
    QColor m_scrim{8, 9, 11};
    qreal m_parallax = 0;
    bool m_running = true;
    bool m_still = false;
    QColor m_frontLight{230, 250, 255};
    QColor m_frontAccent{240, 184, 90};

    bool m_ready = true;
    int m_detail = 4;
    bool m_failed = false;
    qint64 m_estimate = 0;
    qint64 m_budget = 0;
    int m_framesShown = 0;
};
