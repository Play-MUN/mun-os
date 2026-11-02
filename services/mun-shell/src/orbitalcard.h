// OrbitalCard is the card object of the orbital design (MUN Shape v1,
// docs/shape.md, "card"): the card's window image on MUN Disk's disc at the
// orbit's centre, in the card's outline and light. The outline is the disc
// itself for "card" and, for "organic", the disc's edge swelling and
// receding as `morph` grows (0 to 1), held still for a given morph. The
// light is a soft halo in `cardGlow` (invalid: none). `cardReveal`
// (Shape.objectProgress) brings it in as a transition reaches the object:
// the image fades in, and the organic outline and the light grow with it,
// painted in tenths. Without a window (none, or one that did not decode: the
// shell gives a null image) it draws nothing, so MUN's own object stays
// whole (OrbitalFocus shows MUN's icon).
//
// The properties are named as HeroIcon's, the previous design's card
// object, so that the same lookups find either. Painted once per change
// into the item's image: the window scaled to cover the disc and clipped to
// it, the disc's hole cut out (of the light too: it shows what is behind),
// a fine ring at its edge and around the hole.
// The item is the object's square, the disc centred in it with room for the
// light. GUI thread only.
#pragma once

#include <QColor>
#include <QImage>
#include <QQuickPaintedItem>
#include <QString>
#include <QVariant>
#include <QtQml/qqmlregistration.h>

class OrbitalCard : public QQuickPaintedItem {
    Q_OBJECT
    QML_ELEMENT
    // The card's window (a QImage; anything else, none), its outline ("card"
    // or "organic"), how far the organic outline is shaped (0 to 1), its
    // light (invalid: none), and how far the object has become the card's
    // (0 to 1).
    Q_PROPERTY(QVariant cardWindow READ cardWindow WRITE setCardWindow NOTIFY cardChanged)
    Q_PROPERTY(QString cardShape READ cardShape WRITE setCardShape NOTIFY cardChanged)
    Q_PROPERTY(qreal morph READ morph WRITE setMorph NOTIFY cardChanged)
    Q_PROPERTY(QColor cardGlow READ cardGlow WRITE setCardGlow NOTIFY cardChanged)
    Q_PROPERTY(qreal cardReveal READ cardReveal WRITE setCardReveal NOTIFY cardChanged)
    // A window is given: the object is the card's once revealed.
    Q_PROPERTY(bool hasWindow READ hasWindow NOTIFY cardChanged)
    // The disc's radius, in logical pixels.
    Q_PROPERTY(qreal radius READ radius WRITE setRadius NOTIFY cardChanged)

public:
    explicit OrbitalCard(QQuickItem *parent = nullptr);

    void paint(QPainter *painter) override;

    QVariant cardWindow() const { return m_window.isNull() ? QVariant() : QVariant::fromValue(m_window); }
    void setCardWindow(const QVariant &window);
    QString cardShape() const { return m_cardShape; }
    void setCardShape(const QString &shape);
    qreal morph() const { return m_morph; }
    void setMorph(qreal morph);
    QColor cardGlow() const { return m_glow; }
    void setCardGlow(const QColor &glow);
    qreal cardReveal() const { return m_reveal; }
    void setCardReveal(qreal reveal);
    bool hasWindow() const { return !m_window.isNull(); }
    qreal radius() const { return m_radius; }
    void setRadius(qreal radius);

signals:
    void cardChanged();

private:
    // The reveal as painted: in tenths, so a transition repaints ten times.
    int revealStep() const { return int(m_reveal * 10 + 0.5); }

    QImage m_window;
    QString m_cardShape = QStringLiteral("card");
    qreal m_morph = 0;
    QColor m_glow;
    qreal m_reveal = 0;
    qreal m_radius = 84;
};
