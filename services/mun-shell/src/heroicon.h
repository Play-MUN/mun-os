// HeroIcon draws the large object at Home's orb for the entry in focus: the
// Game Card, a fan of cards, a gear, the power sign, a person with a globe, a
// screen with sound, the network's arcs, a chip. When the entry changes the
// new object loads in 0.9 s: a radar-like sweep draws its glowing wireframe,
// its face fills in over the second half and a copper line marks the sweep,
// while the previous object fades out. The crescent on the card (the logo's
// moon) fills while a Game Card is in the slot. The objects are drawn, not
// images, so they stay sharp at any display size.
//
// Each object is painted once into still layers (its shadow, its wireframe
// with the glow, its face, and all of it together) at device resolution, as
// the item shows them and cropped to what they show; a frame then only
// clips, fades and copies them, without transforming a pixel: once loaded,
// one copy. The gear, which turns, keeps its layers in its own frame and
// turns them (one transformed copy a frame), its shadow drawn as vectors. The crescent's layers are painted again as
// it fills, in steps of 2 %.
//
// Motion is paced by SceneClock: 60 frames/s while an object loads, 20 while
// the gear turns or the crescent fills, and the float (6 px over about 10 s)
// is only an offset, `bob`, that QML adds to the item's position: moving the
// painted image costs far less than painting it again. With `running` false
// the gear and the float slow to a stop (Pace); a load in progress still
// finishes.
//
// The item is square and centred on the orb (640 x 640 logical pixels fits
// every object). GUI thread only.
#pragma once

#include "sceneclock.h"

#include <QElapsedTimer>
#include <QHash>
#include <QImage>
#include <QQuickPaintedItem>
#include <QString>
#include <QtQml/qqmlregistration.h>

class HeroIcon : public QQuickPaintedItem {
    Q_OBJECT
    QML_ELEMENT
    // card | games | settings | personal | av | network | system; anything else draws nothing.
    Q_PROPERTY(QString icon READ icon WRITE setIcon NOTIFY iconChanged)
    // How full the card's crescent should be, 0 to 1; it eases there.
    Q_PROPERTY(qreal lit READ lit WRITE setLit NOTIFY litChanged)
    Q_PROPERTY(bool running READ running WRITE setRunning NOTIFY runningChanged)
    // Vertical offset of the float, whole logical pixels.
    Q_PROPERTY(int bob READ bob NOTIFY bobChanged)

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
    int bob() const { return m_bob; }

signals:
    void iconChanged();
    void litChanged();
    void runningChanged();
    void bobChanged();

private:
    void onTick(quint64 frame);
    void updateDemand();
    int rate() const;
    bool loading() const { return m_progress < 1; }
    bool filling() const;
    enum Pass { Shadow = 1, Wire = 2, Face = 4 };
    // A layer cropped to what it shows. `origin` is its top-left corner in
    // device pixels: from the item's corner for a still object's layers
    // (painted as the item shows them, device pixel ratio set), from the
    // object's centre for the turning gear's (painted in its own frame).
    struct Layer {
        QImage image;
        QPointF origin;
    };
    struct Layers {
        Layer shadow, wire, face;
        Layer loaded;          // what the loaded object shows: all of it (the gear: wire and face)
        bool turning = false;  // the gear: painted in its own frame, turned and leaned when drawn
        qreal scale = 0;       // device pixels per logical pixel they were painted at
        qreal lit = -1;        // the crescent they show (the card only)
    };
    // The still layers of `key`, painted now if missing or out of date.
    const Layers &layersFor(const QString &key, qreal scale);
    static Layer cropped(const QImage &image, const QPointF &reference, qreal ratio);
    void drawObject(QPainter *painter, const QString &key, qreal progress, qreal alpha);
    // The object's layers drawn as vectors in its own frame (the painter
    // already there); `item` and `scale` as for its glows (see heroicon.cpp).
    void drawLayers(QPainter *painter, const QTransform &item, qreal scale, qreal sigmaScale, const QString &key,
                    qreal progress, qreal alpha, qreal time, int layers, bool offsetShadow) const;

    QString m_icon = QStringLiteral("card");
    QString m_previous;
    qreal m_progress = 1;     // of the current object's load, 0 to 1
    qreal m_lit = 0.16;       // the crescent as drawn
    qreal m_litTarget = 0.16;
    bool m_running = true;
    Pace m_pace;              // of the gear and the float
    int m_bob = 0;
    qreal m_time = 0;         // seconds of motion shown, for the gear and the float
    QElapsedTimer m_clock;
    QHash<QString, Layers> m_layers;   // of the object in focus and the one fading out
};
