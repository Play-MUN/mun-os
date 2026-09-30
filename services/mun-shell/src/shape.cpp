#include "shape.h"

#include "contrast.h"
#include "readpalette.h"
#include "shapefront.h"

#include <QBuffer>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QImageReader>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>

#include <algorithm>

namespace {

// The card service's exports (services/mun-cardd, "MUN Shape export"): one
// folder per insertion and attempt. Only such a path, for the insertion in
// the record, is ever read.
QString exportRoot()
{
    return qEnvironmentVariable("MUN_SHAPE_ROOT", QStringLiteral("/run/mun/shape"));
}
const QRegularExpression kExportName(QStringLiteral("^([0-9a-f]{16})\\.([0-9]{1,9})$"));
const QRegularExpression kColour(QStringLiteral("^#[0-9A-Fa-f]{6}$"));
// A package path as the card's manifest rules have it (docs/game-cards.md).
const QRegularExpression kPackagePath(QStringLiteral("^[A-Za-z0-9][A-Za-z0-9._-]{0,127}(/[A-Za-z0-9][A-Za-z0-9._-]{0,127})*$"));

constexpr qint64 kDocumentMaxBytes = 256 * 1024;   // the normalised document; the card's own is at most 64 KiB
constexpr qint64 kCoverMaxBytes = 1024 * 1024;
constexpr int kWindowMaxSide = 1024;               // the card window's and the cover's limit
constexpr int kAllocationLimitMiB = 16;            // what one decode may allocate

// MUN's own colours for the dressed surfaces (qml/Theme.qml); the set every
// failing set falls back to, as a whole (docs/shape.md).
const QColor kNeutralPlate(0x17, 0x18, 0x1C);
const QColor kNeutralText(0xDA, 0xD7, 0xD1);
const QColor kNeutralFocus(0xE3, 0x9A, 0x63);   // Theme.copperLight
const QColor kNeutralFocusDeep(0xC2, 0x7B, 0x48);   // Theme.copper
const QColor kNeutralInk(0x13, 0x14, 0x17);     // a chosen entry's label

// A marker in the runtime directory, which outlives the shell's restarts
// (RuntimeDirectoryPreserve=yes in the unit) and not a reboot: `shape-decoding`
// names the insertion being decoded, `shape-cue` the one whose cue is spent.
// Without a runtime directory there are none.
QString runtimeFile(const char *name)
{
    const QString dir = qEnvironmentVariable("XDG_RUNTIME_DIR");
    return dir.isEmpty() ? QString() : dir + QLatin1Char('/') + QLatin1String(name);
}

QString readMarker(const QString &path)
{
    QFile file(path);
    return !path.isEmpty() && file.open(QIODevice::ReadOnly) ? QString::fromLatin1(file.read(64)).trimmed() : QString();
}

void writeMarker(const QString &path, const QString &insertion)
{
    QFile file(path);
    if (!path.isEmpty() && file.open(QIODevice::WriteOnly | QIODevice::Truncate))
        file.write(insertion.toLatin1());
}

bool isColour(const QVariant &value)
{
    return value.typeId() == QMetaType::QString && kColour.match(value.toString()).hasMatch();
}

QColor colourOf(const QVariant &value)
{
    return isColour(value) ? QColor(value.toString()) : QColor();
}

// Decodes an image with its dimensions checked first and Qt's allocation
// limit in force; null if it is not a decodable image within the limits.
QImage decode(QIODevice *device, const QString &what)
{
    QImageReader reader(device, "png");
    const QSize size = reader.size();
    if (!size.isValid() || size.width() < 1 || size.height() < 1 || size.width() > kWindowMaxSide
        || size.height() > kWindowMaxSide) {
        qWarning("mun-shell: shape: %s is not a PNG within %dx%d; not shown", qPrintable(what), kWindowMaxSide,
                 kWindowMaxSide);
        return {};
    }
    QImage image = reader.read();
    if (image.isNull())
        qWarning("mun-shell: shape: %s could not be decoded (%s); not shown", qPrintable(what),
                 qPrintable(reader.errorString()));
    return image;
}

// The palette's lent or read accent becomes the focus on MUN's plate only if
// that set holds at some opacity of MUN's plain plate.
// A colour's hue at another's luminance (sRGB, WCAG's luminance): the
// game's hand-over veil at the luminance of MUN's own, so MUN's hand-over
// text keeps exactly the contrast it has there.
QColor atLuminanceOf(const QColor &hue, const QColor &reference)
{
    const auto linear = [](int c) {
        const double v = c / 255.0;
        return v <= 0.04045 ? v / 12.92 : std::pow((v + 0.055) / 1.055, 2.4);
    };
    const auto encoded = [](double v) {
        v = std::clamp(v, 0.0, 1.0);
        return int(std::lround(255 * (v <= 0.0031308 ? v * 12.92 : 1.055 * std::pow(v, 1 / 2.4) - 0.055)));
    };
    const double target = 0.2126 * linear(reference.red()) + 0.7152 * linear(reference.green()) + 0.0722 * linear(reference.blue());
    const double r = linear(hue.red()), g = linear(hue.green()), b = linear(hue.blue());
    const double own = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    if (own <= 0)
        return reference;
    const double k = target / own;
    return QColor(encoded(r * k), encoded(g * k), encoded(b * k));
}

const QColor kHandOver(0x05, 0x05, 0x06);   // Theme.layer

QColor mixColour(const QColor &a, const QColor &b, qreal t)
{
    t = std::clamp<qreal>(t, 0, 1);
    return QColor(int(std::lround(a.red() + (b.red() - a.red()) * t)), int(std::lround(a.green() + (b.green() - a.green()) * t)),
                  int(std::lround(a.blue() + (b.blue() - a.blue()) * t)));
}

bool holdsOnMunPlate(const QColor &accent)
{
    return accent.isValid()
           && contrast::minimumOpacity(contrast::fromColor(kNeutralPlate), contrast::fromColor(kNeutralText),
                                       contrast::fromColor(accent), QStringLiteral("plain"))
                  .has_value();
}

} // namespace

// ------------------------------------------------------------------ loader

void ShapeLoader::load(quint64 token, const QVariantMap &request)
{
    QVariantMap result;
    QImage window;
    const QString insertion = request.value("insertion").toString();
    const QString path = request.value("path").toString();
    const bool full = request.value("mode").toString() == QLatin1String("full");

    // The crash guard: if decoding below takes the shell down, the next start
    // finds this and skips the insertion (Shape::Shape).
    const QString marker = runtimeFile("shape-decoding");
    writeMarker(marker, insertion);

    QVariantMap document;
    if (!path.isEmpty()) {
        QFile file(path + QStringLiteral("/shape.json"));
        if (file.open(QIODevice::ReadOnly) && file.size() <= kDocumentMaxBytes) {
            QJsonParseError error{};
            const QJsonDocument parsed = QJsonDocument::fromJson(file.read(kDocumentMaxBytes), &error);
            if (error.error == QJsonParseError::NoError && parsed.isObject()) {
                document = parsed.object().toVariantMap();
                if (document.value("insertion").toString() != insertion
                    || document.value("format").toString() != QLatin1String("mun-shape/1")) {
                    qWarning("mun-shell: shape: %s is not this insertion's package; not used", qPrintable(path));
                    document.clear();
                }
            }
        }
        if (document.isEmpty())
            qWarning("mun-shell: shape: no usable shape.json under %s; MUN's look stays", qPrintable(path));
    }
    result.insert("package", document);

    // The card window: the package's, else the cover; only for the full mode.
    // A named image that cannot be read or decoded fails the card block: the
    // object is then MUN's whole (Shape::apply), never the cover and never
    // the block's outline or light around MUN's crescent.
    const QVariantMap card = document.value("card").toMap();
    const QString windowPath = card.value("window").toString();
    bool windowFailed = false;
    if (full && !windowPath.isEmpty()) {
        QFile file(path + QLatin1Char('/') + windowPath);
        if (kPackagePath.match(windowPath).hasMatch() && file.open(QIODevice::ReadOnly))
            window = decode(&file, windowPath);
        else
            qWarning("mun-shell: shape: %s could not be read; not shown", qPrintable(windowPath));
        windowFailed = window.isNull();
    }
    // The cover fills the window when the package names none, and gives the
    // read level its palette when the package has none.
    const bool coverAsWindow = full && windowPath.isEmpty();
    const bool readLevel = !document.contains("palette");
    QImage cover;
    const QByteArray coverBytes = QByteArray::fromBase64(request.value("cover").toString().toLatin1());
    if (!coverBytes.isEmpty() && coverBytes.size() <= kCoverMaxBytes && (coverAsWindow || readLevel)) {
        QBuffer buffer;
        buffer.setData(coverBytes);
        buffer.open(QIODevice::ReadOnly);
        cover = decode(&buffer, QStringLiteral("the cover"));
    }
    if (coverAsWindow) {
        window = cover;
        windowFailed = !coverBytes.isEmpty() && cover.isNull();
    }
    result.insert("windowFailed", windowFailed);
    // The read level, where the package brings no palette.
    if (readLevel && !cover.isNull())
        result.insert("read", readPalette(cover));

    if (!marker.isEmpty())
        QFile::remove(marker);
    emit loaded(token, result, window);
}

// ------------------------------------------------------------------ Shape

Shape::Shape(QObject *parent) : QObject(parent)
{
    const QString marker = runtimeFile("shape-decoding");
    if (QFile::exists(marker)) {
        m_skip = readMarker(marker);
        QFile::remove(marker);
        qWarning("mun-shell: shape: the last start ended while decoding insertion %s; its identity is skipped",
                 qPrintable(m_skip));
    }
    m_cueSpent = readMarker(runtimeFile("shape-cue"));
    // Every decode of the shell's (the card window, the cover, the world) has
    // this limit; set once, before any loader runs.
    QImageReader::setAllocationLimit(kAllocationLimitMiB);
    m_neutralOpacity = contrast::minimumOpacity(contrast::fromColor(kNeutralPlate), contrast::fromColor(kNeutralText),
                                                contrast::fromColor(kNeutralFocus), QStringLiteral("plain"))
                           .value_or(1.0);
    m_loader = new ShapeLoader;
    m_loader->moveToThread(&m_thread);
    connect(&m_thread, &QThread::finished, m_loader, &QObject::deleteLater);
    connect(this, &Shape::loadRequested, m_loader, &ShapeLoader::load, Qt::QueuedConnection);
    connect(m_loader, &ShapeLoader::loaded, this, &Shape::apply, Qt::QueuedConnection);
    m_thread.setObjectName(QStringLiteral("shape-loader"));
    m_thread.start(QThread::LowPriority);
    clear(false);
}

Shape::~Shape()
{
    m_thread.quit();
    m_thread.wait();
}

void Shape::setCard(const QVariantMap &card)
{
    if (card == m_card)
        return;
    m_card = card;
    emit inputsChanged();
    resolve();
}

void Shape::setArrival(const QString &arrival)
{
    // Only read when an identity is applied: no new resolution.
    if (arrival == m_arrival)
        return;
    m_arrival = arrival;
    emit inputsChanged();
}

void Shape::setMode(const QString &mode)
{
    const QString valid = mode == QLatin1String("colours") || mode == QLatin1String("off") ? mode : QStringLiteral("full");
    if (valid == m_mode)
        return;
    m_mode = valid;
    emit inputsChanged();
    resolve();
}

// What a resolution depends on: when none of it changed, nothing is loaded again.
QString Shape::identityKey() const
{
    const QVariantMap info = m_card.value("info").toMap();
    const QVariantMap record = m_card.value("shape").toMap();
    return QStringList{m_mode, m_card.value("insertion").toString(), m_card.value("state").toString(),
                       record.value("state").toString(), record.value("path").toString(),
                       record.value("insertion").toString(), info.value("accent").toString(),
                       info.value("background").toString(), QString::number(qHash(info.value("cover_data").toString()))}
        .join(QLatin1Char('|'));
}

void Shape::resolve()
{
    const QString key = identityKey();
    if (key == m_key)
        return;
    m_key = key;
    const QString insertion = m_card.value("insertion").toString();
    const bool valid = m_card.value("state").toString() == QLatin1String("valid") && m_card.value("active").toBool();
    ++m_token;   // any load in flight is now stale
    m_held = {};
    const bool goes = m_mode == QLatin1String("off") || !valid || insertion.isEmpty() || insertion == m_skip;
    // An identity on screen is not replaced at once: it leaves first, with
    // the transition its reason calls for, and what comes next waits.
    const bool shown = m_phase == QLatin1String("entering") || m_phase == QLatin1String("present")
                       || m_phase == QLatin1String("leaving");
    if (shown) {
        const QString why = m_card.value("state").toString() == QLatin1String("released") ? QStringLiteral("release")
                            : !valid || insertion.isEmpty()                              ? QStringLiteral("removal")
                            : insertion != m_insertion                                   ? QStringLiteral("change")
                                                                                         : QStringLiteral("mode");
        leave(why);
    }
    if (goes) {
        if (!shown) {
            if (m_source != QLatin1String("none"))
                qInfo("mun-shell: shape: back to MUN (%s)", !valid ? "no valid active card" : qPrintable(QStringLiteral("mode ") + m_mode));
            clear(true);
        }
        return;
    }
    const QVariantMap info = m_card.value("info").toMap();
    const QVariantMap record = m_card.value("shape").toMap();
    QString path;
    const QString state = record.value("state").toString();
    if ((state == QLatin1String("ready") || state == QLatin1String("partial"))
        && record.value("insertion").toString() == insertion) {
        const QFileInfo where(record.value("path").toString());
        const auto name = kExportName.match(where.fileName());
        if (where.absolutePath() == QDir(exportRoot()).absolutePath() && name.hasMatch()
            && name.captured(1) == insertion)
            path = where.absoluteFilePath();
        else
            qWarning("mun-shell: shape: the record's path %s is not an export of this insertion; not read",
                     qPrintable(record.value("path").toString()));
    }
    QVariantMap request{{"insertion", insertion}, {"path", path}, {"mode", m_mode},
                        {"cover", info.value("cover_data").toString()}};
    emit loadRequested(m_token, request);
}

void Shape::clear(bool notify)
{
    m_source = QStringLiteral("none");
    m_insertion.clear();
    m_dressed = false;
    m_plate = kNeutralPlate;
    m_text = kNeutralText;
    m_bar = kNeutralText;
    m_barText = kNeutralInk;
    m_entriesOpacity = m_panelOpacity = m_bandsOpacity = 1;
    m_entriesMaterial = m_panelMaterial = QStringLiteral("solid");
    m_focus = kNeutralFocus;
    m_focusDeep = kNeutralFocusDeep;
    m_ambient = m_tint = m_glow = QColor();
    m_window = QImage();
    m_cardShape = QStringLiteral("card");
    m_morph = 0;
    m_sounds.clear();
    m_live = false;
    m_world.clear();
    m_root.clear();
    m_plated = false;
    m_veil = QColor();
    m_transitionIn = m_transitionOut = QStringLiteral("fade");
    m_seconds = 1.6;
    m_entriesPlan = m_panelPlan = m_bandsPlan = m_barPlan = Plan();
    const bool phaseWas = m_phase != QLatin1String("none") || m_progress != 0;
    m_phase = QStringLiteral("none");
    m_entry = QStringLiteral("return");
    m_exit.clear();
    m_progress = 0;
    if (notify) {
        emit changed();
        if (phaseWas) {
            emit progressChanged();
            emit phaseChanged();
        }
    }
}

void Shape::apply(quint64 token, const QVariantMap &result, const QImage &window)
{
    if (token != m_token)
        return;   // a newer card or choice came since
    if (m_phase == QLatin1String("leaving")) {
        m_held = {token, result, window, true};   // shown once the identity on screen has left
        return;
    }
    const QString insertion = m_card.value("insertion").toString();
    const QVariantMap info = m_card.value("info").toMap();
    const QVariantMap document = result.value("package").toMap();
    const QVariantMap palette = document.value("palette").toMap();
    const QVariantMap surfaces = document.value("surfaces").toMap();
    const QVariantMap colours = surfaces.value("colours").toMap();
    const bool full = m_mode == QLatin1String("full");

    clear(false);   // one change: from what was shown to this identity
    m_insertion = insertion;

    // The surfaces: the package's proven set, verified again here; else MUN's.
    bool dressed = colours.value("source").toString() == QLatin1String("shape");
    for (const char *name : {"plate", "text", "focus", "bar", "bar_text"})
        dressed = dressed && isColour(colours.value(name));
    const auto opacityOf = [&](const char *surface) {
        const QVariant value = surfaces.value(surface).toMap().value("opacity");
        bool ok = false;
        const double number = value.toDouble(&ok);
        return ok && number >= 0 && number <= 1 ? number : -1.0;
    };
    const auto materialOf = [&](const char *surface) {
        const QString material = surfaces.value(surface).toMap().value("material").toString();
        return material == QLatin1String("glass") || material == QLatin1String("paper") ? material : QStringLiteral("solid");
    };
    if (dressed) {
        const contrast::Rgb plate = contrast::fromColor(colourOf(colours.value("plate")));
        const contrast::Rgb text = contrast::fromColor(colourOf(colours.value("text")));
        const contrast::Rgb focus = contrast::fromColor(colourOf(colours.value("focus")));
        for (const char *surface : {"entries", "panel", "bands"}) {
            const double opacity = opacityOf(surface);
            const QString material = QLatin1String(surface) == QLatin1String("bands") ? QStringLiteral("glass") : materialOf(surface);
            dressed = dressed && opacity >= 0
                      && contrast::proven({{text, contrast::kTextRatio}, {focus, contrast::kFocusRatio}}, opacity,
                                          contrast::vertices(plate, material));
        }
        dressed = dressed
                  && contrast::ratio(contrast::fromColor(colourOf(colours.value("bar_text"))),
                                     contrast::fromColor(colourOf(colours.value("bar"))))
                         >= contrast::kTextRatio;
        if (!dressed)
            qWarning("mun-shell: shape: insertion %s: the package's surface colours do not hold here; MUN's are used",
                     qPrintable(insertion));
    }
    if (dressed) {
        m_dressed = true;
        m_plate = colourOf(colours.value("plate"));
        m_text = colourOf(colours.value("text"));
        m_bar = colourOf(colours.value("bar"));
        m_barText = colourOf(colours.value("bar_text"));
        m_focus = colourOf(colours.value("focus"));
        m_focusDeep = m_focus.darker(120);
        m_entriesOpacity = opacityOf("entries");
        m_panelOpacity = opacityOf("panel");
        m_bandsOpacity = opacityOf("bands");
        m_entriesMaterial = materialOf("entries");
        m_panelMaterial = materialOf("panel");
    } else {
        // MUN's own set, drawn on plates only where a world is behind it.
        m_entriesOpacity = m_panelOpacity = m_bandsOpacity = m_neutralOpacity;
    }
    // Each surface's plan, as the checker proved it (MUN's set to MUN's: none needed).
    const auto planOf = [&](const char *surface) {
        const QVariantMap entry = surfaces.value(surface).toMap();
        Plan plan;
        const QString name = entry.value("plan").toString();
        if (m_dressed && (name == QLatin1String("neutral-text") || name == QLatin1String("shape-text")
                          || name == QLatin1String("bridge") || name == QLatin1String("cut")))
            plan.plan = name;
        else if (!m_dressed)
            plan.plan = QStringLiteral("neutral-text");
        plan.bridge = colourOf(entry.value("bridge"));
        if (plan.plan == QLatin1String("bridge") && !plan.bridge.isValid())
            plan.plan = QStringLiteral("cut");
        return plan;
    };
    m_entriesPlan = planOf("entries");
    m_panelPlan = planOf("panel");
    m_bandsPlan = planOf("bands");
    m_barPlan = planOf("bar");
    if (m_barPlan.plan == QLatin1String("bridge"))
        m_barPlan.plan = QStringLiteral("cut");

    // The card object, in the full mode: the game's whole (its image, outline
    // and light) only with its image; MUN's whole otherwise, so a card block
    // whose image did not decode is dropped entire, as docs/shape.md drops a
    // block. The sounds are a block of their own.
    const QVariantMap card = document.value("card").toMap();
    if (full && !window.isNull()) {
        m_window = window;
        if (card.value("shape").toString() == QLatin1String("organic")) {
            m_cardShape = QStringLiteral("organic");
            m_morph = std::clamp(card.value("morph").toDouble(), 0.0, 1.0);
        }
        m_glow = colourOf(card.value("glow"));
        if (!m_glow.isValid() && !palette.isEmpty())
            m_glow = colourOf(palette.value("light"));
    }
    if (full) {
        const QVariantMap sounds = document.value("sounds").toMap();
        const QString path = m_card.value("shape").toMap().value("path").toString();
        for (const char *name : {"move", "enter", "back", "insert"}) {
            const QString file = sounds.value(name).toString();
            if (!file.isEmpty() && kPackagePath.match(file).hasMatch())
                m_sounds.insert(QString::fromLatin1(name), path + QLatin1Char('/') + file);
        }
    }

    // Colours where the package has none: the card's lent ones, else the
    // cover's; an accent only where it keeps its contrast on MUN's plate.
    // With a palette, the ambient light is the object's while the object is
    // the game's, else the palette's light: a dropped card block's light
    // reaches nothing.
    const QVariantMap read = result.value("read").toMap();
    const QColor lentAccent = colourOf(info.value("accent"));
    const QColor lentLight = colourOf(info.value("background"));
    const QColor readAccent = colourOf(read.value("accent"));
    QString level = QStringLiteral("none");
    if (!palette.isEmpty()) {
        m_ambient = m_glow.isValid() ? m_glow : colourOf(palette.value("light"));
        m_tint = colourOf(palette.value("mid"));
    } else if (lentAccent.isValid() || lentLight.isValid()) {
        level = QStringLiteral("lent");
        m_ambient = lentLight;
        if (!m_dressed && holdsOnMunPlate(lentAccent)) {
            m_focus = lentAccent;
            m_focusDeep = lentAccent.darker(120);
        }
    } else if (!read.isEmpty()) {
        level = QStringLiteral("read");
        m_ambient = colourOf(read.value("hi"));
        m_tint = colourOf(read.value("mid"));
        if (!m_dressed && holdsOnMunPlate(readAccent)) {
            m_focus = readAccent;
            m_focusDeep = readAccent.darker(120);
        }
    }

    // The world and the transition (full mode: colours only draws no world).
    const QVariantMap world = document.value("world").toMap();
    if (full && !world.isEmpty() && world.contains("backdrop")) {
        m_world = world;
        m_world.insert("bytes", m_card.value("shape").toMap().value("bytes"));
        m_root = m_card.value("shape").toMap().value("path").toString();
    }
    const QVariantMap transition = document.value("transition").toMap();
    const auto kindOf = [](const QVariant &value) {
        const QString kind = value.toString();
        return kind == QLatin1String("tide") || kind == QLatin1String("sweep") ? kind : QStringLiteral("fade");
    };
    m_transitionIn = kindOf(transition.value("in"));
    m_transitionOut = kindOf(transition.value("out"));
    bool ok = false;
    const double seconds = transition.value("seconds").toDouble(&ok);
    m_seconds = ok && seconds >= 0.8 && seconds <= 4 ? seconds : 1.6;
    m_plated = m_dressed || !m_world.isEmpty();
    if (!palette.isEmpty())
        m_veil = atLuminanceOf(colourOf(palette.value("deep")), kHandOver);

    const bool package = !document.isEmpty();
    m_source = package ? QStringLiteral("shape") : level;
    // The insertion's first identity from a package: greeted only if the
    // insertion arrived while the shell watched and its cue is not spent;
    // found at start, as after a game, it is not. However long its copy took.
    const bool first = package && m_adoptedFor != insertion;
    m_live = first && m_arrival == insertion && m_cueSpent != insertion;
    // A package's identity is shown by a transition (QML); the lent and read
    // levels, which only tint MUN, apply at once.
    if (package) {
        m_phase = QStringLiteral("ready");
        m_entry = first && m_arrival == insertion ? QStringLiteral("arrival") : QStringLiteral("return");
    }
    // One line per identity applied: what the laboratory checks it by.
    QStringList said{m_source, m_dressed ? QStringLiteral("dressed: entries %1 %2, panel %3 %4, bands glass %5")
                                               .arg(m_entriesMaterial).arg(m_entriesOpacity, 0, 'f', 3)
                                               .arg(m_panelMaterial).arg(m_panelOpacity, 0, 'f', 3)
                                               .arg(m_bandsOpacity, 0, 'f', 3)
                                         : QStringLiteral("MUN's surfaces"),
                     QStringLiteral("focus %1").arg(m_focus.name(QColor::HexRgb).toUpper())};
    if (!m_window.isNull())
        said << QStringLiteral("window %1x%2").arg(m_window.width()).arg(m_window.height());
    else if (result.value("windowFailed").toBool())
        said << QStringLiteral("card object MUN's (its image did not decode)");
    if (!m_sounds.isEmpty())
        said << QStringLiteral("sounds %1").arg(QStringList(m_sounds.keys()).join(QLatin1Char('/')));
    if (level == QLatin1String("read")) {
        QStringList tones;
        for (const char *tone : {"light", "hi", "mid", "low", "deep", "accent"})
            tones << QStringLiteral("%1 %2").arg(QLatin1String(tone), read.value(tone).toString());
        said << QStringLiteral("read %1").arg(tones.join(QLatin1Char(' ')));
    }
    if (!m_world.isEmpty())
        said << QStringLiteral("world");
    if (package)
        said << QStringLiteral("%1 %2/%3 %4 s").arg(m_entry, m_transitionIn, m_transitionOut).arg(m_seconds, 0, 'f', 1);
    if (first)
        said << QString::fromLatin1(m_live ? "cue due" : "no cue");
    qInfo("mun-shell: shape for insertion %s (%s): %s", qPrintable(insertion), qPrintable(m_mode),
          qPrintable(said.join(QStringLiteral("; "))));
    emit changed();
    emit phaseChanged();
    if (first) {
        // Live or not, the first adoption spends the insertion's cue.
        m_adoptedFor = insertion;
        m_cueSpent = insertion;
        writeMarker(runtimeFile("shape-cue"), insertion);
        emit adopted(m_live);
    }
}

// ------------------------------------------------------------------ presence

void Shape::setPhase(const QString &phase)
{
    if (phase == m_phase)
        return;
    m_phase = phase;
    emit phaseChanged();
}

void Shape::setProgress(qreal progress)
{
    progress = std::clamp<qreal>(std::isfinite(progress) ? progress : 0, 0, 1);
    if (qFuzzyCompare(progress + 1, m_progress + 1))
        return;
    m_progress = progress;
    emit progressChanged();
}

void Shape::setOrb(const QPointF &orb)
{
    if (orb == m_orb)
        return;
    m_orb = orb;
    emit progressChanged();
}

void Shape::begin(const QString &kind)
{
    if (m_phase != QLatin1String("ready"))
        return;
    m_kind = kind == QLatin1String("tide") || kind == QLatin1String("sweep") ? kind : QStringLiteral("fade");
    m_progress = 0;
    qInfo("mun-shell: shape: insertion %s comes in (%s, %s)", qPrintable(m_insertion), qPrintable(m_entry), qPrintable(m_kind));
    setPhase(QStringLiteral("entering"));
    emit progressChanged();
}

void Shape::arrived()
{
    if (m_phase != QLatin1String("entering"))
        return;
    m_progress = 1;
    setPhase(QStringLiteral("present"));
    emit progressChanged();
}

void Shape::leave(const QString &why)
{
    if (m_phase == QLatin1String("leaving"))
        return;   // already on its way: the first reason stands
    m_exit = why;
    qInfo("mun-shell: shape: insertion %s leaves (%s)", qPrintable(m_insertion), qPrintable(why));
    setPhase(QStringLiteral("leaving"));
}

void Shape::leaveWith(const QString &kind)
{
    if (m_phase != QLatin1String("leaving"))
        return;
    const QString valid = kind == QLatin1String("tide") || kind == QLatin1String("sweep") ? kind : QStringLiteral("fade");
    if (valid != m_kind) {
        m_kind = valid;
        emit phaseChanged();
        emit progressChanged();
    }
}

void Shape::left()
{
    if (m_phase != QLatin1String("leaving"))
        return;
    const Held held = m_held;
    m_held = {};
    clear(true);
    if (held.set && held.token == m_token)
        apply(held.token, held.result, held.window);
}

qreal Shape::reach(const QString &phase, const QString &kind, qreal progress, const QRectF &box) const
{
    if (phase == QLatin1String("present"))
        return 1;
    if (phase != QLatin1String("entering") && phase != QLatin1String("leaving"))
        return 0;
    return shapefront::reach(kind, m_orb, progress, box);
}

qreal Shape::objectProgress() const
{
    if (m_phase == QLatin1String("present"))
        return 1;
    if (m_phase != QLatin1String("entering") && m_phase != QLatin1String("leaving"))
        return 0;
    return shapefront::objectReach(m_kind, m_orb, m_progress);
}

QVariantMap Shape::blend(const QString &surface, qreal u) const
{
    // docs/shape.md, "Contrast": only the plate blends. Its opacity rises to
    // the higher of both ends' (to opaque for a bridge) in the first fifth,
    // its colour blends in the next three (through the bridge at the middle),
    // its opacity settles in the last; the text and focus change at one point:
    // the colour blend's end (neutral-text), its start (shape-text), the
    // bridge (bridge) or the middle (cut, where the whole surface changes).
    u = std::clamp<qreal>(u, 0, 1);
    if (surface == QLatin1String("neutral")) {
        // One of MUN's own panels over a game's world: MUN's set on a plate of
        // its proven opacity, from the moment the front reaches it.
        return {{QStringLiteral("plate"), kNeutralPlate},
                {QStringLiteral("opacity"), m_neutralOpacity},
                {QStringLiteral("material"), QStringLiteral("solid")},
                {QStringLiteral("amount"), 0.0},
                {QStringLiteral("game"), false}};
    }
    const bool bar = surface == QLatin1String("bar");
    const Plan &plan = bar ? m_barPlan : surface == QLatin1String("panel") ? m_panelPlan
                                        : surface == QLatin1String("bands") ? m_bandsPlan : m_entriesPlan;
    const QColor fromPlate = bar ? kNeutralText : kNeutralPlate;
    const QColor toPlate = bar ? m_bar : m_plate;
    const qreal fromOpacity = bar ? 1 : m_neutralOpacity;
    const qreal toOpacity = bar ? 1
                            : surface == QLatin1String("panel") ? m_panelOpacity
                            : surface == QLatin1String("bands") ? m_bandsOpacity : m_entriesOpacity;
    const QString material = bar ? QStringLiteral("solid")
                             : surface == QLatin1String("panel") ? m_panelMaterial
                             : surface == QLatin1String("bands") ? QStringLiteral("glass") : m_entriesMaterial;
    const auto step = [](qreal u, qreal from, qreal to) { return std::clamp<qreal>((u - from) / (to - from), 0, 1); };

    QColor plate;
    qreal opacity, amount;
    bool game;
    if (plan.plan == QLatin1String("cut")) {
        game = u >= 0.5;
        plate = game ? toPlate : fromPlate;
        opacity = game ? toOpacity : fromOpacity;
        amount = game ? 1 : 0;
    } else if (plan.plan == QLatin1String("bridge")) {
        const qreal rise = step(u, 0, 0.2), settle = step(u, 0.8, 1);
        opacity = u < 0.8 ? fromOpacity + (1 - fromOpacity) * rise : 1 + (toOpacity - 1) * settle;
        plate = u < 0.5 ? mixColour(fromPlate, plan.bridge, step(u, 0.2, 0.5)) : mixColour(plan.bridge, toPlate, step(u, 0.5, 0.8));
        amount = step(u, 0.5, 0.8);
        game = u >= 0.5;
    } else {
        const qreal held = std::max(fromOpacity, toOpacity);
        const qreal rise = step(u, 0, 0.2), settle = step(u, 0.8, 1);
        opacity = u < 0.8 ? fromOpacity + (held - fromOpacity) * rise : held + (toOpacity - held) * settle;
        plate = mixColour(fromPlate, toPlate, step(u, 0.2, 0.8));
        amount = step(u, 0.2, 0.8);
        game = plan.plan == QLatin1String("shape-text") ? u >= 0.2 : u >= 0.8;
    }
    return {{QStringLiteral("plate"), plate},
            {QStringLiteral("opacity"), opacity},
            {QStringLiteral("material"), material},
            {QStringLiteral("amount"), amount},
            {QStringLiteral("game"), game}};
}
