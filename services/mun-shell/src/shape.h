// Shape brings the active Game Card's identity to the shell's eligible
// surfaces (docs/shape.md): the main arc's entries, the game's panel, the
// bands of the status line, the path and the hints, the card object, the
// ambient light and tint of Home's world, and the menus' sounds. Settings,
// their panels and every dialog stay MUN's; they never read this object.
//
// What it uses, for the valid active card only, in this order:
// - The card service's export of the card's package (the record's `shape`,
//   `ready` or `partial`, for the same insertion, under /run/mun/shape/):
//   its normalised shape.json, the card window it names, its sounds. The
//   surfaces' colours and opacities come from that document, which the card
//   service proved with the checker; they are verified again here with the
//   same rule (contrast.h) and replaced by MUN's, as a set, if they fail.
// - Where the package declares no palette, the card's lent colours
//   ([presentation] accent and background); with neither, the palette read
//   from its cover (readpalette.h), the accent becoming the focus only if it
//   keeps 3:1 on MUN's plate.
// - Mode "colours" keeps the colours and leaves out every image and sound;
//   "off" is MUN's alone.
//
// Nothing is decoded on the GUI thread: the export's files and the cover are
// read and decoded by a worker (ShapeLoader) on its own thread, with the
// image's dimensions checked before it is decoded and an allocation limit.
// Every result carries the load's token and is dropped if a newer load, or
// another card, has come since. A decoder that crashes the shell cannot do it
// in a loop: a marker in the runtime directory names the insertion being
// decoded, and the next start skips that insertion's identity.
//
// GUI thread only; the loader's thread is joined by the destructor.
#pragma once

#include <QColor>
#include <QElapsedTimer>
#include <QImage>
#include <QObject>
#include <QString>
#include <QThread>
#include <QVariant>
#include <QVariantMap>
#include <QtQml/qqmlregistration.h>

class ShapeLoader;

class Shape : public QObject {
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    // Inputs, bound from QML: the active card's record (CardClient.card) and
    // the player's choice ("full", "colours" or "off").
    Q_PROPERTY(QVariantMap card READ card WRITE setCard NOTIFY inputsChanged)
    Q_PROPERTY(QString mode READ mode WRITE setMode NOTIFY inputsChanged)

    // What is applied. `source`: "none" (MUN), "lent", "read" or "shape".
    Q_PROPERTY(QString source READ source NOTIFY changed)
    Q_PROPERTY(QString insertion READ insertion NOTIFY changed)
    // The eligible surfaces take the game's colours and plates.
    Q_PROPERTY(bool dressed READ dressed NOTIFY changed)
    Q_PROPERTY(QColor plate READ plate NOTIFY changed)
    Q_PROPERTY(QColor text READ text NOTIFY changed)
    Q_PROPERTY(QColor bar READ bar NOTIFY changed)
    Q_PROPERTY(QColor barText READ barText NOTIFY changed)
    Q_PROPERTY(qreal entriesOpacity READ entriesOpacity NOTIFY changed)
    Q_PROPERTY(qreal panelOpacity READ panelOpacity NOTIFY changed)
    Q_PROPERTY(qreal bandsOpacity READ bandsOpacity NOTIFY changed)
    Q_PROPERTY(QString entriesMaterial READ entriesMaterial NOTIFY changed)
    Q_PROPERTY(QString panelMaterial READ panelMaterial NOTIFY changed)
    // The focus on eligible surfaces: the game's, a lent or read accent that
    // keeps its contrast, or MUN's copper. `focusDeep` is its darker shade.
    Q_PROPERTY(QColor focus READ focus NOTIFY changed)
    Q_PROPERTY(QColor focusDeep READ focusDeep NOTIFY changed)
    // Home's world: the ambient light's colour and a tint of its glows;
    // invalid means MUN's own.
    Q_PROPERTY(QColor ambient READ ambient NOTIFY changed)
    Q_PROPERTY(QColor tint READ tint NOTIFY changed)
    // The card object: its window (a QImage, null for MUN's crescent), its
    // outline ("card" or "organic"), how far that is shaped, its light.
    Q_PROPERTY(QVariant window READ window NOTIFY changed)
    Q_PROPERTY(QString cardShape READ cardShape NOTIFY changed)
    Q_PROPERTY(qreal morph READ morph NOTIFY changed)
    Q_PROPERTY(QColor glow READ glow NOTIFY changed)
    // The game's menu sounds: name -> absolute path of a file of the export.
    Q_PROPERTY(QVariantMap sounds READ sounds NOTIFY changed)
    // The identity arrived while the shell was running (not found at its
    // start, as after a game): it may be greeted with the insertion cue.
    Q_PROPERTY(bool live READ live NOTIFY changed)

public:
    explicit Shape(QObject *parent = nullptr);
    ~Shape() override;

    QVariantMap card() const { return m_card; }
    void setCard(const QVariantMap &card);
    QString mode() const { return m_mode; }
    void setMode(const QString &mode);

    QString source() const { return m_source; }
    QString insertion() const { return m_insertion; }
    bool dressed() const { return m_dressed; }
    QColor plate() const { return m_plate; }
    QColor text() const { return m_text; }
    QColor bar() const { return m_bar; }
    QColor barText() const { return m_barText; }
    qreal entriesOpacity() const { return m_entriesOpacity; }
    qreal panelOpacity() const { return m_panelOpacity; }
    qreal bandsOpacity() const { return m_bandsOpacity; }
    QString entriesMaterial() const { return m_entriesMaterial; }
    QString panelMaterial() const { return m_panelMaterial; }
    QColor focus() const { return m_focus; }
    QColor focusDeep() const { return m_focusDeep; }
    QColor ambient() const { return m_ambient; }
    QColor tint() const { return m_tint; }
    QVariant window() const { return m_window.isNull() ? QVariant() : QVariant::fromValue(m_window); }
    QString cardShape() const { return m_cardShape; }
    qreal morph() const { return m_morph; }
    QColor glow() const { return m_glow; }
    QVariantMap sounds() const { return m_sounds; }
    bool live() const { return m_live; }

signals:
    void inputsChanged();
    void changed();
    // A new identity from a package was applied for this insertion.
    void adopted(bool live);
    // To the loader's thread.
    void loadRequested(quint64 token, const QVariantMap &request);

private:
    void resolve();
    void clear(bool notify);
    void apply(quint64 token, const QVariantMap &result, const QImage &window);
    QString identityKey() const;

    QVariantMap m_card;
    QString m_mode = QStringLiteral("full");
    QString m_key;             // what the current resolution was made for
    quint64 m_token = 0;       // of the newest load
    QString m_skip;            // an insertion whose decoding crashed the last start
    QString m_adoptedFor;      // the insertion the last `adopted` was for
    QElapsedTimer m_age;       // since the shell started, for `live`
    QThread m_thread;
    ShapeLoader *m_loader = nullptr;

    QString m_source = QStringLiteral("none");
    QString m_insertion;
    bool m_dressed = false;
    QColor m_plate, m_text, m_bar, m_barText;
    qreal m_entriesOpacity = 1, m_panelOpacity = 1, m_bandsOpacity = 1;
    QString m_entriesMaterial = QStringLiteral("solid"), m_panelMaterial = QStringLiteral("solid");
    QColor m_focus, m_focusDeep, m_ambient, m_tint, m_glow;
    QImage m_window;
    QString m_cardShape = QStringLiteral("card");
    qreal m_morph = 0;
    QVariantMap m_sounds;
    bool m_live = false;
};

// Reads and decodes on its own thread; see Shape.
class ShapeLoader : public QObject {
    Q_OBJECT
public slots:
    void load(quint64 token, const QVariantMap &request);
signals:
    void loaded(quint64 token, const QVariantMap &result, const QImage &window);
};
