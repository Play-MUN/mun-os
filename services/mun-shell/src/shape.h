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
// The card object is the package's whole or MUN's whole: its image (the
// package's window, else the cover), outline and light together, or MUN's
// crescent with none of them when that image is missing or does not decode.
// The ambient light follows the object's light while the object is the
// game's, and the palette's light otherwise.
//
// Presence (S3): an identity from a package is shown and taken away by a
// transition (docs/shape.md, "transition"), which QML runs: `phase` goes
// "ready" (applied, not shown yet) → begin() → "entering" → arrived() →
// "present", and, when the identity must go, "leaving" → left() → "none".
// While it leaves, every value stays the leaving identity's, and a new card's
// result waits (held) until left(). `entry` says how it is to come in: an
// "arrival" (the insertion arrived while the shell watched: the package's
// transition) or a "return" (found at start, as after a game, or shown again
// after a choice: a short fade). `exit` says why it leaves: "release" (Eject
// safely confirmed: the package's out transition), "removal", "change" (another
// card) or "mode" (a short one). `progress` is the transition's position, 0 to
// 1, written by QML's animation; reach() is how far it has reached a surface,
// and blend() the plate a surface shows there, by its proven plan.
//
// The insertion cue: the first identity from a package applied for an
// insertion is `live` only if that insertion arrived while the shell was
// watching (`arrival`, from CardClient: not already active in the card
// service's snapshot), whatever time its copy took. That first adoption
// spends the insertion's cue, live or not, and a marker in the runtime
// directory (`shape-cue`) keeps it spent across the shell's restarts, so an
// insertion is greeted at most once however a later connection observes it.
//
// GUI thread only; the loader's thread is joined by the destructor.
#pragma once

#include <QColor>
#include <QImage>
#include <QObject>
#include <QPointF>
#include <QRectF>
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

    // Inputs, bound from QML: the active card's record (CardClient.card),
    // the player's choice ("full", "colours" or "off") and the insertion
    // that arrived while the shell was watching (CardClient.arrival).
    Q_PROPERTY(QVariantMap card READ card WRITE setCard NOTIFY inputsChanged)
    Q_PROPERTY(QString mode READ mode WRITE setMode NOTIFY inputsChanged)
    Q_PROPERTY(QString arrival READ arrival WRITE setArrival NOTIFY inputsChanged)

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
    // outline ("card" or "organic"), how far that is shaped, its light
    // (invalid: none). MUN's crescent never takes a game's outline or light.
    Q_PROPERTY(QVariant window READ window NOTIFY changed)
    Q_PROPERTY(QString cardShape READ cardShape NOTIFY changed)
    Q_PROPERTY(qreal morph READ morph NOTIFY changed)
    Q_PROPERTY(QColor glow READ glow NOTIFY changed)
    // The game's menu sounds: name -> absolute path of a file of the export.
    Q_PROPERTY(QVariantMap sounds READ sounds NOTIFY changed)
    // Presence (above).
    Q_PROPERTY(QString phase READ phase NOTIFY phaseChanged)
    Q_PROPERTY(QString entry READ entry NOTIFY phaseChanged)
    Q_PROPERTY(QString exit READ exit NOTIFY phaseChanged)
    Q_PROPERTY(qreal progress READ progress WRITE setProgress NOTIFY progressChanged)
    // The transition running ("tide", "fade", "sweep"), set by begin() and leave().
    Q_PROPERTY(QString kind READ kind NOTIFY phaseChanged)
    // The package's transition: in, out and seconds (docs/shape.md).
    Q_PROPERTY(QString transitionIn READ transitionIn NOTIFY changed)
    Q_PROPERTY(QString transitionOut READ transitionOut NOTIFY changed)
    Q_PROPERTY(qreal seconds READ seconds NOTIFY changed)
    // The card object's own progress: it changes first.
    Q_PROPERTY(qreal objectProgress READ objectProgress NOTIFY progressChanged)
    Q_PROPERTY(QPointF orb READ orb WRITE setOrb NOTIFY progressChanged)
    // The world (full mode): the export's normalised block with the export's
    // size ("bytes"), and the export's folder. Empty: none.
    Q_PROPERTY(QVariantMap world READ world NOTIFY changed)
    Q_PROPERTY(QString root READ root NOTIFY changed)
    // The eligible surfaces sit on plates: the game's set, or MUN's own on
    // plates of their proven opacity when a world is drawn behind them.
    Q_PROPERTY(bool plated READ plated NOTIFY changed)
    Q_PROPERTY(qreal neutralOpacity READ neutralOpacity NOTIFY changed)
    // A colour for the game's hand-over veil on which MUN's hand-over text
    // keeps its contrast (the palette's deep, else invalid: MUN's).
    Q_PROPERTY(QColor veil READ veil NOTIFY changed)
    // The identity is its insertion's first, and the insertion arrived while
    // the shell was watching (not found active, as at start or after a
    // game), its cue not spent: it may be greeted with the insertion cue.
    Q_PROPERTY(bool live READ live NOTIFY changed)

public:
    explicit Shape(QObject *parent = nullptr);
    ~Shape() override;

    QVariantMap card() const { return m_card; }
    void setCard(const QVariantMap &card);
    QString mode() const { return m_mode; }
    void setMode(const QString &mode);
    QString arrival() const { return m_arrival; }
    void setArrival(const QString &arrival);

    QString phase() const { return m_phase; }
    QString entry() const { return m_entry; }
    QString exit() const { return m_exit; }
    qreal progress() const { return m_progress; }
    void setProgress(qreal progress);
    QString kind() const { return m_kind; }
    QString transitionIn() const { return m_transitionIn; }
    QString transitionOut() const { return m_transitionOut; }
    qreal seconds() const { return m_seconds; }
    qreal objectProgress() const;
    QPointF orb() const { return m_orb; }
    void setOrb(const QPointF &orb);
    QVariantMap world() const { return m_world; }
    QString root() const { return m_root; }
    bool plated() const { return m_plated; }
    qreal neutralOpacity() const { return m_neutralOpacity; }
    QColor veil() const { return m_veil; }

    // QML runs the transitions: the identity begins to show with `kind`,
    // has arrived, has left.
    Q_INVOKABLE void begin(const QString &kind);
    Q_INVOKABLE void arrived();
    Q_INVOKABLE void left();
    // Starts the leaving of the identity shown with `kind` (QML chooses it
    // from `exit`); called by QML when phase becomes "leaving".
    Q_INVOKABLE void leaveWith(const QString &kind);
    // How far a transition in `phase`, of `kind`, at `progress` has reached
    // `box` (canvas coordinates); QML passes Shape's own phase, kind and
    // progress, so that its bindings follow them.
    Q_INVOKABLE qreal reach(const QString &phase, const QString &kind, qreal progress, const QRectF &box) const;
    // The plate `surface` ("entries", "panel", "bands") shows at local
    // progress `u`: {plate, opacity, material, amount, game}, `game` true once
    // its text and focus are the game's; for "bar", {plate, game}; for
    // "neutral", MUN's own plate (a MUN panel over the game's world).
    Q_INVOKABLE QVariantMap blend(const QString &surface, qreal u) const;

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
    void phaseChanged();
    void progressChanged();
    // The first identity from a package was applied for this insertion.
    void adopted(bool live);
    // To the loader's thread.
    void loadRequested(quint64 token, const QVariantMap &request);

private:
    void resolve();
    void clear(bool notify);
    void apply(quint64 token, const QVariantMap &result, const QImage &window);
    void leave(const QString &why);
    void setPhase(const QString &phase);
    QString identityKey() const;

    QVariantMap m_card;
    QString m_mode = QStringLiteral("full");
    QString m_key;             // what the current resolution was made for
    quint64 m_token = 0;       // of the newest load
    QString m_skip;            // an insertion whose decoding crashed the last start
    QString m_arrival;         // the insertion that arrived while the shell watched
    QString m_adoptedFor;      // the insertion the last `adopted` was for
    QString m_cueSpent;        // the insertion whose cue is spent (the `shape-cue` marker)
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

    // Presence.
    QString m_phase = QStringLiteral("none");
    QString m_entry = QStringLiteral("return");
    QString m_exit;
    QString m_kind = QStringLiteral("fade");
    qreal m_progress = 0;
    QPointF m_orb{470, 570};
    QString m_transitionIn = QStringLiteral("fade"), m_transitionOut = QStringLiteral("fade");
    qreal m_seconds = 1.6;
    QVariantMap m_world;
    QString m_root;
    bool m_plated = false;
    qreal m_neutralOpacity = 1;
    QColor m_veil;
    // Each surface's plan (docs/shape.md, "Contrast"): plan and bridge colour.
    struct Plan {
        QString plan = QStringLiteral("cut");
        QColor bridge;
    };
    Plan m_entriesPlan, m_panelPlan, m_bandsPlan, m_barPlan;
    // A result that came while the shown identity was leaving.
    struct Held {
        quint64 token = 0;
        QVariantMap result;
        QImage window;
        bool set = false;
    };
    Held m_held;
};

// Reads and decodes on its own thread; see Shape.
class ShapeLoader : public QObject {
    Q_OBJECT
public slots:
    void load(quint64 token, const QVariantMap &request);
signals:
    void loaded(quint64 token, const QVariantMap &result, const QImage &window);
};
