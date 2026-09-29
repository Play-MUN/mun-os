#include "displaymode.h"

#include <QByteArray>
#include <QDir>
#include <QFile>
#include <QHash>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QList>
#include <QRegularExpression>
#include <QSaveFile>

#include <algorithm>
#include <optional>

namespace display {

const QStringList kModes{QStringLiteral("1280x720"), QStringLiteral("1920x1080"), QStringLiteral("2560x1440")};
const QString kResolutionKey = QStringLiteral("display/resolution");
const QString kTrialKey = QStringLiteral("display/resolution-trial");

namespace {

Plan g_plan;

// The interface is laid out on a 1920x1080 logical canvas, the design's own
// pixels (qml/Theme.qml).
constexpr double kCanvasWidth = 1920;
constexpr double kCanvasHeight = 1080;

// VESA CVT reduced-blanking timings at 60 Hz (what `cvt -r W H` prints), in
// the order of Qt's modelines: pixel clock in MHz, then horizontal display,
// sync start, sync end and total, the same vertically, and the sync
// polarities. Only a virtual connector gets them.
const QHash<QString, QString> kModelines{
    {QStringLiteral("1280x720"), QStringLiteral("64.00 1280 1328 1360 1440 720 723 728 741 +hsync -vsync")},
    {QStringLiteral("1920x1080"), QStringLiteral("138.50 1920 1968 2000 2080 1080 1083 1088 1111 +hsync -vsync")},
    {QStringLiteral("2560x1440"), QStringLiteral("241.50 2560 2608 2640 2720 1440 1443 1448 1481 +hsync -vsync")},
};

// The kernel's connector types, as sysfs spells them ("card0-HDMI-A-1"), to
// the names Qt's KMS configuration uses for the same outputs ("HDMI1"): Qt
// names by its own table, in which several kernel types share a name. A type
// Qt does not name is not configured.
const QHash<QString, QString> kQtTypeNames{
    {QStringLiteral("Unknown"), QStringLiteral("None")},
    {QStringLiteral("VGA"), QStringLiteral("VGA")},
    {QStringLiteral("DVI-I"), QStringLiteral("DVI")},
    {QStringLiteral("DVI-D"), QStringLiteral("DVI")},
    {QStringLiteral("DVI-A"), QStringLiteral("DVI")},
    {QStringLiteral("Composite"), QStringLiteral("Composite")},
    {QStringLiteral("SVIDEO"), QStringLiteral("TV")},
    {QStringLiteral("LVDS"), QStringLiteral("LVDS")},
    {QStringLiteral("Component"), QStringLiteral("CTV")},
    {QStringLiteral("DIN"), QStringLiteral("DIN")},
    {QStringLiteral("DP"), QStringLiteral("DP")},
    {QStringLiteral("HDMI-A"), QStringLiteral("HDMI")},
    {QStringLiteral("HDMI-B"), QStringLiteral("HDMI")},
    {QStringLiteral("TV"), QStringLiteral("TV")},
    {QStringLiteral("eDP"), QStringLiteral("eDP")},
    {QStringLiteral("Virtual"), QStringLiteral("Virtual")},
    {QStringLiteral("DSI"), QStringLiteral("DSI")},
};

QByteArray readFile(const QString &path, qint64 limit = 64 * 1024)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly))
        return {};
    return file.read(limit);
}

struct Connector {
    QString qtName;
    QStringList modes;  // "1920x1080", the preferred one first
    bool isVirtual = false;
};

// The first connected connector of card0, in name order.
std::optional<Connector> firstConnector()
{
    static const QRegularExpression entryRe(QStringLiteral("^card0-(.+)-(\\d+)$"));
    static const QRegularExpression modeRe(QStringLiteral("^\\d{3,5}x\\d{3,5}$"));
    const QDir drm(QStringLiteral("/sys/class/drm"));
    const QStringList entries = drm.entryList({QStringLiteral("card0-*")}, QDir::Dirs | QDir::NoDotAndDotDot, QDir::Name);
    for (const QString &entry : entries) {
        if (readFile(drm.filePath(entry + QStringLiteral("/status"))).trimmed() != "connected")
            continue;
        const QRegularExpressionMatch match = entryRe.match(entry);
        const QString type = match.hasMatch() ? kQtTypeNames.value(match.captured(1)) : QString();
        if (type.isEmpty())
            continue;
        Connector connector;
        connector.qtName = type + match.captured(2);
        connector.isVirtual = match.captured(1) == QStringLiteral("Virtual");
        // One mode per line, sorted by the kernel with the preferred first;
        // interlaced ones ("1920x1080i") are not offered.
        for (const QByteArray &line : readFile(drm.filePath(entry + QStringLiteral("/modes"))).split('\n')) {
            const QString mode = QString::fromLatin1(line.trimmed());
            if (modeRe.match(mode).hasMatch() && !connector.modes.contains(mode))
                connector.modes << mode;
        }
        return connector;
    }
    return std::nullopt;
}

void setScale(int width, int height)
{
    if (width <= 0 || height <= 0)
        return;
    const double factor = std::min(width / kCanvasWidth, height / kCanvasHeight);
    qputenv("QT_SCALE_FACTOR", QByteArray::number(factor, 'g', 6));
}

// "auto": the display's preferred mode, which the kernel's framebuffer
// console also uses, so its size is the size Qt will get.
void scaleToFramebuffer()
{
    const QList<QByteArray> parts = readFile(QStringLiteral("/sys/class/graphics/fb0/virtual_size")).trimmed().split(',');
    if (parts.size() == 2)
        setScale(parts[0].toInt(), parts[1].toInt());
}

// Qt's KMS configuration for `mode` on the plan's connector, in the runtime
// directory; its path, or empty if it could not be written.
QString writeKmsConfig(const QString &runtime, const Plan &plan, const QString &mode, bool listed)
{
    const QJsonObject output{{QStringLiteral("name"), plan.connector},
                             {QStringLiteral("mode"), listed ? mode : kModelines.value(mode)}};
    const QJsonObject config{{QStringLiteral("device"), QStringLiteral("/dev/dri/card0")},
                             {QStringLiteral("outputs"), QJsonArray{output}}};
    QSaveFile file(runtime + QStringLiteral("/kms.json"));
    if (!file.open(QIODevice::WriteOnly) || file.write(QJsonDocument(config).toJson()) < 0 || !file.commit())
        return {};
    return file.fileName();
}

QString takeResume(const QString &runtime)
{
    if (runtime.isEmpty())
        return {};
    QFile marker(runtime + QStringLiteral("/resume"));
    if (!marker.exists())
        return {};
    const QString where = QString::fromLatin1(readFile(marker.fileName(), 32).trimmed());
    marker.remove();
    return where == QStringLiteral("resolution") || where == QStringLiteral("reset") ? where : QString();
}

} // namespace

void prepare(QSettings &settings)
{
    Plan result;
    const QString runtime = qEnvironmentVariable("XDG_RUNTIME_DIR");
    result.resume = takeResume(runtime);

    const bool scaleGiven = qEnvironmentVariableIsSet("QT_SCALE_FACTOR");
    const QString platform = qEnvironmentVariable("QT_QPA_PLATFORM");
    const bool kms = platform.startsWith(QStringLiteral("linuxfb")) || platform.startsWith(QStringLiteral("eglfs"));
    // A KMS configuration someone else wrote (a hardware integration) owns
    // the display: the shell then offers no resolution of its own.
    const bool configGiven = qEnvironmentVariableIsSet("QT_QPA_KMS_CONFIG") || qEnvironmentVariableIsSet("QT_QPA_EGLFS_KMS_CONFIG");
    if (!kms || configGiven || runtime.isEmpty()) {
        if (!scaleGiven)
            scaleToFramebuffer();
        g_plan = result;
        return;
    }

    const QString stored = settings.value(kResolutionKey).toString();
    // The trial is used once: gone from the file before Qt opens the display.
    // If it cannot be removed, it is not used, or every start would try it.
    QString trial = settings.value(kTrialKey).toString();
    if (settings.contains(kTrialKey)) {
        settings.remove(kTrialKey);
        settings.sync();
        if (settings.status() != QSettings::NoError)
            trial.clear();
    }

    std::optional<Connector> connector = firstConnector();
    if (connector) {
        result.connector = connector->qtName;
        result.preferred = connector->modes.value(0);
        for (const QString &mode : kModes) {
            if (connector->isVirtual || connector->modes.contains(mode))
                result.offered << mode;
        }
    }
    if (result.offered.contains(trial) && trial != stored) {
        result.active = trial;
        result.trial = true;
    } else if (result.offered.contains(stored)) {
        result.active = stored;
    }

    if (result.active != QStringLiteral("auto")) {
        const QString path = writeKmsConfig(runtime, result, result.active, connector->modes.contains(result.active));
        if (path.isEmpty()) {
            qWarning("mun-shell: could not write the display configuration; the display's own mode applies");
            result.active = QStringLiteral("auto");
            result.trial = false;
        } else {
            qputenv("QT_QPA_KMS_CONFIG", QFile::encodeName(path));
            if (!scaleGiven) {
                const QStringList size = result.active.split(QLatin1Char('x'));
                setScale(size[0].toInt(), size[1].toInt());
            }
        }
    }
    if (result.active == QStringLiteral("auto")) {
        // No configuration of an earlier start is left looking current.
        QFile::remove(runtime + QStringLiteral("/kms.json"));
        if (!scaleGiven)
            scaleToFramebuffer();
    }
    g_plan = result;
}

const Plan &plan()
{
    return g_plan;
}

void setResume(const QString &where)
{
    const QString runtime = qEnvironmentVariable("XDG_RUNTIME_DIR");
    if (runtime.isEmpty())
        return;
    QSaveFile marker(runtime + QStringLiteral("/resume"));
    if (marker.open(QIODevice::WriteOnly)) {
        marker.write(where.toLatin1());
        marker.commit();
    }
}

} // namespace display
