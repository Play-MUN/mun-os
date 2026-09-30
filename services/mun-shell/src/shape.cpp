#include "shape.h"

#include "contrast.h"
#include "readpalette.h"

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

QString markerPath()
{
    const QString dir = qEnvironmentVariable("XDG_RUNTIME_DIR");
    return dir.isEmpty() ? QString() : dir + QStringLiteral("/shape-decoding");
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
    QImageReader::setAllocationLimit(kAllocationLimitMiB);
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
    const QString marker = markerPath();
    if (!marker.isEmpty()) {
        QFile file(marker);
        if (file.open(QIODevice::WriteOnly | QIODevice::Truncate))
            file.write(insertion.toLatin1());
    }

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
    const QVariantMap card = document.value("card").toMap();
    const QString windowPath = card.value("window").toString();
    if (full && !windowPath.isEmpty() && kPackagePath.match(windowPath).hasMatch()) {
        QFile file(path + QLatin1Char('/') + windowPath);
        if (file.open(QIODevice::ReadOnly))
            window = decode(&file, windowPath);
    }
    // The cover fills the window when the package names none (a window that
    // fails to decode leaves MUN's object, not the cover), and gives the read
    // level its palette when the package has none.
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
    if (coverAsWindow)
        window = cover;
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
    m_age.start();
    const QString marker = markerPath();
    if (!marker.isEmpty()) {
        QFile file(marker);
        if (file.open(QIODevice::ReadOnly)) {
            m_skip = QString::fromLatin1(file.read(64)).trimmed();
            file.close();
            file.remove();
            qWarning("mun-shell: shape: the last start ended while decoding insertion %s; its identity is skipped",
                     qPrintable(m_skip));
        }
    }
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
    if (m_mode == QLatin1String("off") || !valid || insertion.isEmpty() || insertion == m_skip) {
        if (m_source != QLatin1String("none"))
            qInfo("mun-shell: shape: back to MUN (%s)", !valid ? "no valid active card" : qPrintable(QStringLiteral("mode ") + m_mode));
        clear(true);
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
    if (notify)
        emit changed();
}

void Shape::apply(quint64 token, const QVariantMap &result, const QImage &window)
{
    if (token != m_token)
        return;   // a newer card or choice came since
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
    }

    // Colours where the package has none: the card's lent ones, else the
    // cover's; an accent only where it keeps its contrast on MUN's plate.
    const QVariantMap read = result.value("read").toMap();
    const QColor lentAccent = colourOf(info.value("accent"));
    const QColor lentLight = colourOf(info.value("background"));
    const QColor readAccent = colourOf(read.value("accent"));
    QString level = QStringLiteral("none");
    if (!palette.isEmpty()) {
        m_ambient = colourOf(document.value("card").toMap().value("glow"));
        if (!m_ambient.isValid())
            m_ambient = colourOf(palette.value("light"));
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

    // The card object and the sounds: the package's, in the full mode.
    const QVariantMap card = document.value("card").toMap();
    if (full) {
        m_window = window;
        if (card.value("shape").toString() == QLatin1String("organic")) {
            m_cardShape = QStringLiteral("organic");
            m_morph = std::clamp(card.value("morph").toDouble(), 0.0, 1.0);
        }
        m_glow = colourOf(card.value("glow"));
        if (!m_glow.isValid() && !palette.isEmpty())
            m_glow = colourOf(palette.value("light"));
        const QVariantMap sounds = document.value("sounds").toMap();
        const QString path = m_card.value("shape").toMap().value("path").toString();
        for (const char *name : {"move", "enter", "back", "insert"}) {
            const QString file = sounds.value(name).toString();
            if (!file.isEmpty() && kPackagePath.match(file).hasMatch())
                m_sounds.insert(QString::fromLatin1(name), path + QLatin1Char('/') + file);
        }
    }

    const bool package = !document.isEmpty();
    m_source = package ? QStringLiteral("shape") : level;
    const bool first = package && m_adoptedFor != insertion;
    // Found at start (as after a game) or arrived while running.
    m_live = first ? m_age.elapsed() > 5000 : false;
    // One line per identity applied: what the laboratory checks it by.
    QStringList said{m_source, m_dressed ? QStringLiteral("dressed: entries %1 %2, panel %3 %4, bands glass %5")
                                               .arg(m_entriesMaterial).arg(m_entriesOpacity, 0, 'f', 3)
                                               .arg(m_panelMaterial).arg(m_panelOpacity, 0, 'f', 3)
                                               .arg(m_bandsOpacity, 0, 'f', 3)
                                         : QStringLiteral("MUN's surfaces"),
                     QStringLiteral("focus %1").arg(m_focus.name(QColor::HexRgb).toUpper())};
    if (!m_window.isNull())
        said << QStringLiteral("window %1x%2").arg(m_window.width()).arg(m_window.height());
    if (!m_sounds.isEmpty())
        said << QStringLiteral("sounds %1").arg(QStringList(m_sounds.keys()).join(QLatin1Char('/')));
    if (level == QLatin1String("read")) {
        QStringList tones;
        for (const char *tone : {"light", "hi", "mid", "low", "deep", "accent"})
            tones << QStringLiteral("%1 %2").arg(QLatin1String(tone), read.value(tone).toString());
        said << QStringLiteral("read %1").arg(tones.join(QLatin1Char(' ')));
    }
    qInfo("mun-shell: shape for insertion %s (%s): %s", qPrintable(insertion), qPrintable(m_mode),
          qPrintable(said.join(QStringLiteral("; "))));
    emit changed();
    if (first) {
        m_adoptedFor = insertion;
        emit adopted(m_live);
    }
}
