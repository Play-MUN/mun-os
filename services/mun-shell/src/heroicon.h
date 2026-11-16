// HeroIcon draws the large object at Home's orb for the entry in focus: the
// Game Card, a fan of cards, a gear, the power sign; and Settings' sections: a
// screen, a speaker, the logo's moon in its eclipse, a globe with a clock, the
// network's arcs, a chip. When the entry changes the
// new object loads in 0.9 s: a radar-like sweep draws its glowing wireframe,
// its face fills in over the second half and a copper line marks the sweep,
// while the previous object fades out. The crescent on the card (the logo's
// moon) fills while a Game Card is in the slot. The objects are drawn, not
// images, so they stay sharp at any display size.
//
// Each object is painted into still layers (its shadow, its wireframe with
// the glow, its face, and all of it together) at device resolution, as the
// item shows them and cropped to what they show; a frame then only clips,
// fades and places them, without painting the object again. The gear, which
// turns, keeps its layers in its own frame and turns them. The crescent's
// layers are painted again as it fills, in steps of 2 %. Two drawing paths
// (src/renderinfo.h):
// - Qt Quick's software renderer: the layers are painted when a frame first
//   needs them, on the thread that renders, and a frame copies them through
//   QPainter (the turning gear: one transformed copy, its shadow drawn as
//   vectors).
// - A graphics API: the layers are painted on a thread of their own, never
//   where frames are made, and each becomes a texture, the image let go once
//   uploaded; until an object's layers are ready the previous object stays
//   and its load waits. A frame sets opacities, the load's sweep (a clip),
//   the gear's turn (a transform) and the copper line (a little geometry),
//   which the GPU applies. Only the layers of the object in focus and of the
//   one fading out are kept, as on the software path.
//
// Motion is paced by SceneClock: 60 frames/s while an object loads; on its
// timer 20 while the gear turns or the crescent fills, and the float (6 px
// over about 10 s) moves in whole pixels at 5; on a synced clock (an
// accelerated renderer) the gear and the float move on every frame, the
// float by fractions of a pixel. The float is only an offset, `bob`, that QML
// adds to the item's position: moving the drawn object costs far less than
// drawing it again. With `running` false the gear and the float slow to a
// stop (Pace); a load in progress still finishes.
//
// A Game Card's MUN Shape (docs/shape.md) dresses the card object: its
// screen shows the card's window image (or its cover) instead of the
// crescent, its outline may be organic, shaped by `morph`, and its light
// takes the game's colour. Still: the outline does not move, and the layers
// are painted again only when these change.
//
// The item is square and centred on the orb (640 x 640 logical pixels fits
// every object). Properties and motion on the GUI thread; the painting
// thread works on copies (Look) and hands its layers back queued; the
// destructor waits for it.
#pragma once

#include "sceneclock.h"

#include <QColor>
#include <QElapsedTimer>
#include <QHash>
#include <QImage>
#include <QVariant>
#include <QQuickPaintedItem>
#include <QString>
#include <QThreadPool>
#include <QtQml/qqmlregistration.h>

class HeroIcon : public QQuickPaintedItem {
    Q_OBJECT
    QML_ELEMENT
    // card | games | settings | power | display | sound | appearance | language | network | system;
    // anything else draws nothing.
    Q_PROPERTY(QString icon READ icon WRITE setIcon NOTIFY iconChanged)
    // How full the card's crescent should be, 0 to 1; it eases there.
    Q_PROPERTY(qreal lit READ lit WRITE setLit NOTIFY litChanged)
    Q_PROPERTY(bool running READ running WRITE setRunning NOTIFY runningChanged)
    // Vertical offset of the float in logical pixels: whole on SceneClock's
    // timer, fractional on a synced clock.
    Q_PROPERTY(qreal bob READ bob NOTIFY bobChanged)
    // The card object's MUN Shape: the image its screen shows (a QImage;
    // anything else, MUN's crescent), its outline ("card" or "organic"), how
    // far the organic outline is shaped (0 to 1), its light (invalid: MUN's).
    Q_PROPERTY(QVariant cardWindow READ cardWindow WRITE setCardWindow NOTIFY cardChanged)
    Q_PROPERTY(QString cardShape READ cardShape WRITE setCardShape NOTIFY cardChanged)
    Q_PROPERTY(qreal morph READ morph WRITE setMorph NOTIFY cardChanged)
    Q_PROPERTY(QColor cardGlow READ cardGlow WRITE setCardGlow NOTIFY cardChanged)
    // How far the card object has become its Shape, 0 (MUN's crescent) to 1
    // (its window, outline and light), as a transition reaches it; painted
    // in tenths.
    Q_PROPERTY(qreal cardReveal READ cardReveal WRITE setCardReveal NOTIFY cardChanged)

public:
    explicit HeroIcon(QQuickItem *parent = nullptr);
    ~HeroIcon() override;

    void paint(QPainter *painter) override;

    QString icon() const { return m_icon; }
    void setIcon(const QString &icon);
    qreal lit() const { return m_litTarget; }
    void setLit(qreal lit);
    bool running() const { return m_running; }
    void setRunning(bool running);
    qreal bob() const { return m_bob; }
    QVariant cardWindow() const { return m_window.isNull() ? QVariant() : QVariant::fromValue(m_window); }
    void setCardWindow(const QVariant &window);
    QString cardShape() const { return m_cardShape; }
    void setCardShape(const QString &shape);
    qreal morph() const { return m_morph; }
    void setMorph(qreal morph);
    QColor cardGlow() const { return m_cardGlow; }
    void setCardGlow(const QColor &glow);
    qreal cardReveal() const { return m_reveal; }
    void setCardReveal(qreal reveal);

    enum Pass { Shadow = 1, Wire = 2, Face = 4 };
    // A layer cropped to what it shows. `origin` is its top-left corner in
    // device pixels: from the item's corner for a still object's layers
    // (painted as the item shows them, device pixel ratio set), from the
    // object's centre for the turning gear's (painted in its own frame).
    // `id` names the picture; it outlives the image, which the graphics
    // path lets go once its texture has it.
    struct Layer {
        QImage image;
        QPointF origin;
        QSize size;
        qint64 id = 0;
    };
    struct Layers {
        Layer shadow, wire, face;
        Layer loaded;          // what the loaded object shows: all of it (the gear: wire and face)
        bool turning = false;  // the gear: painted in its own frame, turned and leaned when drawn
        qreal scale = 0;       // device pixels per logical pixel they were painted at
        qreal lit = -1;        // the crescent they show (the card only)
        quint64 look = 0;      // the graphics path's: the card's styleKey() they were painted with
    };
    // What the card's layers depend on besides the key and the scale: a copy
    // the painting thread can work on.
    struct Look {
        QImage window;
        QString cardShape;
        qreal morph = 0;
        QColor cardGlow;
        qreal reveal = 1;
        qreal lit = 0.16;
    };

signals:
    void iconChanged();
    void litChanged();
    void runningChanged();
    void bobChanged();
    void cardChanged();

protected:
    QSGNode *updatePaintNode(QSGNode *old, UpdatePaintNodeData *data) override;
    void releaseResources() override;

private:
    void onTick(quint64 frame);
    void updateDemand();
    int rate() const;
    bool loading() const { return m_progress < 1; }
    bool filling() const;
    Look look() const;
    // The graphics path (on the GUI thread): whether it draws, whether
    // `key`'s layers are there to show and whether they are out of date,
    // asking the painting thread for those shown that are, and taking them
    // back.
    bool onGpu() const;
    quint64 styleKey() const;
    bool ready(const QString &key) const;
    bool stale(const QString &key, qreal scale) const;
    void request();
    void painted(const QString &key, const Layers &layers, qint64 ms);
    // The software path's still layers of `key`, painted now if missing or
    // out of date.
    const Layers &layersFor(const QString &key, qreal scale);
    // `composite`: the graphics path's (`loaded` composed of the others, the
    // gear's shadow as a layer of its own).
    static Layers paintLayers(const QString &key, const Look &look, qreal scale, const QSizeF &size, bool composite);
    static Layer cropped(const QImage &image, const QPointF &reference, qreal ratio);
    void drawObject(QPainter *painter, const QString &key, qreal progress, qreal alpha);
    // The object's layers drawn as vectors in its own frame (the painter
    // already there); `item` and `scale` as for its glows (see heroicon.cpp).
    static void drawLayers(QPainter *painter, const QTransform &item, qreal scale, qreal sigmaScale,
                           const QString &key, const Look &look, qreal progress, qreal alpha, qreal time,
                           int layers, bool offsetShadow);

    QString m_icon = QStringLiteral("card");
    QString m_previous;
    qreal m_progress = 1;     // of the current object's load, 0 to 1
    qreal m_lit = 0.16;       // the crescent as drawn
    qreal m_litTarget = 0.16;
    bool m_running = true;
    Pace m_pace;              // of the gear and the float
    qreal m_bob = 0;
    qreal m_time = 0;         // seconds of motion shown, for the gear and the float
    qreal m_last = -1;        // SceneClock's seconds at the last frame; -1 after a pause
    QHash<QString, Layers> m_layers;   // of the object in focus and the one fading out
    QHash<QString, bool> m_asked;      // the graphics path's: keys the painting thread is working on
    void cardRestyled();
    QImage m_window;          // the card's screen, from its Shape
    QString m_cardShape = QStringLiteral("card");
    qreal m_morph = 0;
    QColor m_cardGlow;
    qreal m_reveal = 1;
    // Last: destroyed first, waiting for a painting in progress while the
    // rest is still whole.
    QThreadPool m_painter;
};
