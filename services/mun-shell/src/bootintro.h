// BootIntro draws one part of MUN's start-up: the console waking in the
// dark, over the start-up sound (sounds/startup.wav, 13.05 s). In the
// sound's time, t:
//
//   0.30  a pim: the amber LED, a point that breathes once, then grows
//         (1.35 to 3.3 s) into the console's long oval, two sweeps of light
//         running along it;
//   3.45  a hit: the screen shakes and flashes, a faint light behind the
//         console shows only the top of its lid, and a gleam runs along the
//         MUN engraved on its dome;
//   6.65  MUN, higher up, with a ding and a discharge from the LED that makes
//         the letters' strokes tremble for about 2.3 s, a warm glow rising;
//   7.85  the moon out of an eclipse: copper, then the shadow withdraws with
//         a soft edge and leaves the arc silver;
//  10.05  the LED melts away in silence, from amber to ember, and the logo
//         fades while the sound rings on to 13.05 s.
//
// The whole picture is a function of t (the design's own frame function,
// whose time leads the sound's by 0.35 s): QML sets t from the sound's
// clock on every frame and each part paints what t shows. A part is painted
// again only when what it shows has changed, and only over its own `area`;
// the still layers (the screen's gradient, the silhouette, the tube's
// vignette, the finished logo) are painted once per display scale.
//
// The parts, in drawing order (QML stacks them): Screen, the background;
// Silhouette, the lid's top in the light behind it; Engraving, the gleam on
// the dome; Mark, the letters and the moon; Led; Tube, the screen's vignette
// over everything. Between Screen and Tube the parts shake together
// (`shake`), fade together at the end (`fade`), and a white flash covers
// them (`flash`): QML applies those three, read from any part.
//
// The picture stands still from start to end: the console, its LED and the
// U of MUN on the screen's vertical centre line (the moon reaches out to the
// right, as the logo draws it), and the finished logo with the LED under it
// centred on the screen.
//
// With `breath` at 0 or more, a Led part draws the LED waiting for the
// system instead, at full length and as bright as `breath` (0 to 1) says.
// `reduceMotion` takes out the screen's shake, the letters' tremor and the
// flicker; every time stays.
//
// The design's units: an SVG view "176 -22 1414 900", drawn 1180 px wide on
// the 1920 x 1080 canvas; `area` and the item's geometry are in
// canvas coordinates (the item's parent spans the canvas). Its random
// tremor and flicker are drawn from t, so a given t always looks the same.
// GUI thread only.
#pragma once

#include <QColor>
#include <QImage>
#include <QPainterPath>
#include <QPointF>
#include <QQuickPaintedItem>
#include <QRectF>
#include <QtQml/qqmlregistration.h>

#include <vector>

class BootIntro : public QQuickPaintedItem {
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(Part part READ part WRITE setPart NOTIFY partChanged)
    // Seconds into the start-up sound.
    Q_PROPERTY(qreal t READ t WRITE setT NOTIFY tChanged)
    Q_PROPERTY(bool reduceMotion READ reduceMotion WRITE setReduceMotion NOTIFY reduceMotionChanged)
    // The waiting LED's brightness, 0 to 1; below 0 (the default), not waiting.
    Q_PROPERTY(qreal breath READ breath WRITE setBreath NOTIFY breathChanged)
    // Where this part paints, in canvas coordinates.
    Q_PROPERTY(QRectF area READ area NOTIFY partChanged)
    // At t, for the layers QML moves: the screen's offset in canvas pixels,
    // the flash's opacity and the picture's opacity as it fades at the end.
    Q_PROPERTY(QPointF shake READ shake NOTIFY tChanged)
    Q_PROPERTY(qreal flash READ flash NOTIFY tChanged)
    Q_PROPERTY(qreal fade READ fade NOTIFY tChanged)
    // When the picture has faded (the sound rings on), and the sound's end.
    Q_PROPERTY(qreal faded READ faded CONSTANT)
    Q_PROPERTY(qreal end READ end CONSTANT)

public:
    enum Part { Screen, Silhouette, Engraving, Mark, Led, Tube };
    Q_ENUM(Part)

    static constexpr qreal kFaded = 11.75;
    static constexpr qreal kEnd = 13.05;

    explicit BootIntro(QQuickItem *parent = nullptr);

    void paint(QPainter *painter) override;

    Part part() const { return m_part; }
    void setPart(Part part);
    qreal t() const { return m_t; }
    void setT(qreal t);
    bool reduceMotion() const { return m_reduceMotion; }
    void setReduceMotion(bool on);
    qreal breath() const { return m_breath; }
    void setBreath(qreal breath);
    QRectF area() const;
    QPointF shake() const;
    qreal flash() const;
    qreal fade() const;
    qreal faded() const { return kFaded; }
    qreal end() const { return kEnd; }

signals:
    void partChanged();
    void tChanged();
    void reduceMotionChanged();
    void breathChanged();

private:
    struct Layer {
        QImage image;      // device pixels, premultiplied
        QPointF origin;    // its top left, in device pixels of the item
        qreal scale = 0;   // the device scale it was painted at
    };

    // What this part shows at the current t, rounded to what can be seen:
    // equal keys paint the same picture.
    QByteArray stateKey() const;
    void refresh();

    void paintScreen(QPainter *painter, qreal scale);
    void paintSilhouette(QPainter *painter, qreal scale);
    void paintEngraving(QPainter *painter, qreal scale);
    void paintMark(QPainter *painter, qreal scale);
    void paintLed(QPainter *painter, qreal scale);
    void paintTube(QPainter *painter, qreal scale);

    // A transparent layer covering the item at `scale`.
    QImage offscreen(qreal scale) const;
    // Makes the still layer of a Silhouette or an Engraving at `scale`.
    void prepare(qreal scale);

    Part m_part = Screen;
    qreal m_t = 0;
    bool m_reduceMotion = false;
    qreal m_breath = -1;
    QByteArray m_painted;    // the stateKey() of what is on screen
    Layer m_still;           // a still layer, kept per display scale
    Layer m_finished;        // Mark: the logo once nothing in it moves
};
