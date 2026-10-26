#include "displaymode.h"

#include <QByteArray>
#include <QElapsedTimer>
#include <QFile>
#include <QHash>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QList>
#include <QSaveFile>
#include <QSize>

#include <algorithm>
#include <chrono>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <unistd.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

namespace display {

const QStringList kModes{QStringLiteral("1280x720"), QStringLiteral("1920x1080"), QStringLiteral("2560x1440")};
const QString kResolutionKey = QStringLiteral("display/resolution");
const QString kTrialKey = QStringLiteral("display/resolution-trial");

namespace {

Plan g_plan;
// The display awaitDisplay() found, and the exact mode asked of Qt when the
// shell chose it from the connector's own list (the cap).
std::optional<Display> g_display;
std::optional<Timing> g_requested;

static_assert(kFlagPHSync == DRM_MODE_FLAG_PHSYNC && kFlagNHSync == DRM_MODE_FLAG_NHSYNC &&
              kFlagPVSync == DRM_MODE_FLAG_PVSYNC && kFlagNVSync == DRM_MODE_FLAG_NVSYNC &&
              kFlagInterlace == DRM_MODE_FLAG_INTERLACE && kFlagDoubleScan == DRM_MODE_FLAG_DBLSCAN,
              "displaydevice.h's flags are the kernel's");

const char kDrmClass[] = "/sys/class/drm";
const char kGraphicsClass[] = "/sys/class/graphics";
constexpr int kDefaultWaitSeconds = 30;
constexpr int kMaxWaitSeconds = 600;

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

// The kernel's connector types, as sysfs and libdrm spell them ("HDMI-A-1"),
// to the names Qt's KMS configuration uses for the same outputs ("HDMI1"): Qt
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

QString devicePath()
{
    return QStringLiteral("/dev/dri/") + QString::fromLatin1(kCard.data(), int(kCard.size()));
}

// Qt draws through its framebuffer platform on DRM (the unit's setting):
// the case in which it opens card0 and nothing else.
bool drawsOnCard0()
{
    return qEnvironmentVariable("QT_QPA_PLATFORM").startsWith(QStringLiteral("linuxfb")) &&
           qEnvironmentVariableIntValue("QT_QPA_FB_DRM") != 0;
}

// card0, opened for reading what it reports; closed again before Qt opens
// it, so the shell never holds the display's master role.
class Card0
{
public:
    Card0() : m_fd(::open(QFile::encodeName(devicePath()).constData(), O_RDWR | O_CLOEXEC)) {}
    ~Card0()
    {
        if (m_fd >= 0)
            ::close(m_fd);
    }
    Card0(const Card0 &) = delete;
    Card0 &operator=(const Card0 &) = delete;
    int fd() const { return m_fd; }

    // The connector the kernel calls `name` ("HDMI-A-1"). `probe` reads it
    // from the display again (its EDID, its state); otherwise what the
    // kernel last saw. Freed by the caller (drmModeFreeConnector).
    drmModeConnectorPtr connector(const std::string &name, bool probe) const
    {
        drmModeResPtr resources = m_fd >= 0 ? drmModeGetResources(m_fd) : nullptr;
        if (!resources)
            return nullptr;
        uint32_t id = 0;
        for (int i = 0; i < resources->count_connectors && !id; ++i) {
            drmModeConnectorPtr current = drmModeGetConnectorCurrent(m_fd, resources->connectors[i]);
            if (!current)
                continue;
            const char *type = drmModeGetConnectorTypeName(current->connector_type);
            if (type && name == std::string(type) + "-" + std::to_string(current->connector_type_id))
                id = current->connector_id;
            drmModeFreeConnector(current);
        }
        drmModeFreeResources(resources);
        return !id ? nullptr : probe ? drmModeGetConnector(m_fd, id) : drmModeGetConnectorCurrent(m_fd, id);
    }

    // Has the kernel read every connector again. Without a client that
    // has (the kernel's own console on DRM, or a hotplug), a connector's
    // state in sysfs can still read "unknown".
    void probeAll() const
    {
        drmModeResPtr resources = m_fd >= 0 ? drmModeGetResources(m_fd) : nullptr;
        if (!resources)
            return;
        for (int i = 0; i < resources->count_connectors; ++i) {
            if (drmModeConnectorPtr connector = drmModeGetConnector(m_fd, resources->connectors[i]))
                drmModeFreeConnector(connector);
        }
        drmModeFreeResources(resources);
    }

private:
    int m_fd;
};

Timing timingOf(const drmModeModeInfo &mode)
{
    Timing timing;
    timing.clock = mode.clock;
    timing.hdisplay = mode.hdisplay;
    timing.hsyncStart = mode.hsync_start;
    timing.hsyncEnd = mode.hsync_end;
    timing.htotal = mode.htotal;
    timing.vdisplay = mode.vdisplay;
    timing.vsyncStart = mode.vsync_start;
    timing.vsyncEnd = mode.vsync_end;
    timing.vtotal = mode.vtotal;
    timing.flags = mode.flags;
    timing.preferred = mode.type & DRM_MODE_TYPE_PREFERRED;
    return timing;
}

bool sameTiming(const Timing &a, const Timing &b)
{
    return a.clock == b.clock && a.hdisplay == b.hdisplay && a.hsyncStart == b.hsyncStart && a.hsyncEnd == b.hsyncEnd &&
           a.htotal == b.htotal && a.vdisplay == b.vdisplay && a.vsyncStart == b.vsyncStart && a.vsyncEnd == b.vsyncEnd &&
           a.vtotal == b.vtotal;
}

// The connector's modes, in its order (the preferred first), read from the
// display.
std::vector<Timing> readModes(const std::string &output)
{
    std::vector<Timing> modes;
    Card0 card;
    drmModeConnectorPtr connector = card.connector(output, true);
    if (!connector)
        return modes;
    for (int i = 0; i < connector->count_modes; ++i)
        modes.push_back(timingOf(connector->modes[i]));
    drmModeFreeConnector(connector);
    return modes;
}

// Qt's name for the kernel's connector ("HDMI-A-1" is "HDMI1"); empty for a
// type Qt does not name.
QString qtName(const QString &output)
{
    const qsizetype dash = output.lastIndexOf(QLatin1Char('-'));
    if (dash <= 0)
        return {};
    const QString type = kQtTypeNames.value(output.left(dash));
    return type.isEmpty() ? QString() : type + output.mid(dash + 1);
}

QString text(const std::string &value)
{
    return QString::fromStdString(value);
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

// Qt's KMS configuration for `mode` ("1920x1080" or a modeline) on the
// plan's connector, in the runtime directory; its path, or empty if it could
// not be written. Qt 6.8's framebuffer platform ignores its device (card0
// regardless); it is written for the platforms that read it.
QString writeKmsConfig(const QString &runtime, const Plan &plan, const QString &mode)
{
    const QJsonObject output{{QStringLiteral("name"), plan.connector}, {QStringLiteral("mode"), mode}};
    const QJsonObject config{{QStringLiteral("device"), devicePath()},
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

int waitSeconds()
{
    if (!qEnvironmentVariableIsSet("MUN_DISPLAY_WAIT_SECONDS"))
        return kDefaultWaitSeconds;
    bool ok = false;
    const int seconds = qEnvironmentVariableIntValue("MUN_DISPLAY_WAIT_SECONDS", &ok);
    if (ok && seconds >= 0 && seconds <= kMaxWaitSeconds)
        return seconds;
    qWarning("mun-shell: display: MUN_DISPLAY_WAIT_SECONDS is not a number of seconds from 0 to %d; %d applies",
             kMaxWaitSeconds, kDefaultWaitSeconds);
    return kDefaultWaitSeconds;
}

// The board's cap for Automatic, if the environment gives one.
std::optional<Cap> cap()
{
    if (!qEnvironmentVariableIsSet("MUN_DISPLAY_MAX"))
        return std::nullopt;
    const QByteArray value = qgetenv("MUN_DISPLAY_MAX");
    const std::optional<Cap> parsed = parseCap(std::string_view(value.constData(), size_t(value.size())));
    if (!parsed)
        qWarning("mun-shell: display: MUN_DISPLAY_MAX \"%s\" is not WIDTHxHEIGHT@HZ; no cap applies", value.constData());
    return parsed;
}

QString pickNote(Pick pick, const Cap &limit)
{
    const QString capText = QStringLiteral("%1x%2@%3").arg(limit.width).arg(limit.height).arg(limit.hz);
    switch (pick) {
    case Pick::Target:
        return QStringLiteral("the cap %1").arg(capText);
    case Pick::Fallback:
        return QStringLiteral("a fallback: the display lists no %1x%2 at %3 Hz").arg(limit.width).arg(limit.height).arg(limit.hz);
    case Pick::AboveCap:
        return QStringLiteral("above the cap %1: no listed mode fits it").arg(capText);
    }
    return {};
}

} // namespace

bool awaitDisplay()
{
    if (!drawsOnCard0())
        return true;
    const int seconds = waitSeconds();
    QElapsedTimer waited;
    waited.start();
    qint64 lastProbe = -1;
    bool announced = false;
    for (;;) {
        std::vector<Card> cards = scanCards(kDrmClass);
        g_display = usableDisplay(cards);
        if (g_display) {
            const QString after = announced ? QStringLiteral(" after %1 s").arg(waited.elapsed() / 1000.0, 0, 'f', 1)
                                            : QString();
            qInfo("mun-shell: display: %s %s (%s)%s", g_display->card.c_str(), g_display->connector.c_str(),
                  g_display->driver.empty() ? "driver unknown" : g_display->driver.c_str(), qPrintable(after));
            return true;
        }
        const qint64 elapsed = waited.elapsed();
        if (elapsed >= qint64(seconds) * 1000) {
            qCritical("mun-shell: display: no usable display after %d s; exiting to be started again", seconds);
            for (const std::string &line : describe(cards, kGraphicsClass))
                qWarning("mun-shell: display:   %s", line.c_str());
            return false;
        }
        if (!announced) {
            qInfo("mun-shell: display: waiting up to %d s for a display on %s", seconds, kCard.data());
            announced = true;
        }
        // card0 is the real driver's, but no connector reads as connected
        // yet: have the kernel look again, every two seconds at most.
        const auto card0 = std::find_if(cards.begin(), cards.end(), [](const Card &card) { return card.name == kCard; });
        if (card0 != cards.end() && !card0->firmware && !card0->connectors.empty() &&
            (lastProbe < 0 || elapsed - lastProbe >= 2000)) {
            Card0().probeAll();
            lastProbe = elapsed;
            continue;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
    }
}

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

    // eglfs, or a start that did not wait: card0 as it is now.
    if (!g_display)
        g_display = usableDisplay(scanCards(kDrmClass));
    std::vector<Timing> timings;
    QStringList sizes; // "1920x1080", the preferred one first, once each
    bool isVirtual = false;
    if (g_display) {
        result.output = text(g_display->connector);
        result.connector = qtName(result.output);
        isVirtual = result.output.startsWith(QStringLiteral("Virtual-"));
        timings = readModes(g_display->connector);
        if (timings.empty())
            qWarning("mun-shell: display: %s lists no modes", g_display->connector.c_str());
        // Interlaced modes are not offered.
        for (const Timing &timing : timings) {
            const QString size = QStringLiteral("%1x%2").arg(timing.hdisplay).arg(timing.vdisplay);
            if (!(timing.flags & kFlagInterlace) && !sizes.contains(size))
                sizes << size;
        }
    }
    if (!result.connector.isEmpty()) {
        result.preferred = sizes.value(0);
        for (const QString &mode : kModes) {
            if (isVirtual || sizes.contains(mode))
                result.offered << mode;
        }
    }
    if (result.offered.contains(trial) && trial != stored) {
        result.active = trial;
        result.trial = true;
    } else if (result.offered.contains(stored)) {
        result.active = stored;
    }

    // What Qt is asked for: the player's mode, by size when the display
    // lists it (Qt takes the first of that size) or as a CVT modeline on a
    // virtual connector; under Automatic with a cap, the chosen mode as its
    // own modeline; otherwise nothing, and Qt takes the display's own mode.
    QString mode;
    QSize size;
    if (result.active != QStringLiteral("auto")) {
        mode = sizes.contains(result.active) ? result.active : kModelines.value(result.active);
        const QStringList parts = result.active.split(QLatin1Char('x'));
        size = QSize(parts[0].toInt(), parts[1].toInt());
        result.requested = result.active + (result.trial ? QStringLiteral(" (the player's choice, on trial)")
                                                        : QStringLiteral(" (the player's choice)"));
    } else if (const std::optional<Cap> limit = cap(); limit && !result.connector.isEmpty()) {
        const std::optional<Choice> choice = chooseMode(timings, *limit);
        const std::optional<std::string> line = choice ? modeline(timings[choice->index]) : std::nullopt;
        if (line) {
            const Timing &chosen = timings[choice->index];
            mode = text(*line);
            size = QSize(chosen.hdisplay, chosen.vdisplay);
            g_requested = chosen;
            result.requested = QStringLiteral("%1 (%2)").arg(text(modeName(chosen)), pickNote(choice->pick, *limit));
            if (choice->pick != Pick::Target)
                qWarning("mun-shell: display: %s", qPrintable(result.requested));
        } else {
            qWarning("mun-shell: display: no mode for the cap %s; the display's own mode applies",
                     choice ? "can be written as a modeline" : "among the listed ones");
        }
    }

    if (!mode.isEmpty()) {
        const QString path = writeKmsConfig(runtime, result, mode);
        if (path.isEmpty()) {
            qWarning("mun-shell: could not write the display configuration; the display's own mode applies");
            result.active = QStringLiteral("auto");
            result.trial = false;
            mode.clear();
            g_requested.reset();
        } else {
            qputenv("QT_QPA_KMS_CONFIG", QFile::encodeName(path));
            if (!scaleGiven)
                setScale(size.width(), size.height());
        }
    }
    if (mode.isEmpty()) {
        result.requested = QStringLiteral("the display's own mode");
        // No configuration of an earlier start is left looking current.
        QFile::remove(runtime + QStringLiteral("/kms.json"));
        if (!scaleGiven)
            scaleToFramebuffer();
    }
    g_plan = result;
}

void reportApplied()
{
    if (!g_display || g_plan.output.isEmpty() || !drawsOnCard0())
        return;
    const std::string output = g_display->connector;
    Card0 card;
    drmModeConnectorPtr connector = card.connector(output, false);
    drmModeEncoderPtr encoder = connector && connector->encoder_id ? drmModeGetEncoder(card.fd(), connector->encoder_id) : nullptr;
    drmModeCrtcPtr crtc = encoder && encoder->crtc_id ? drmModeGetCrtc(card.fd(), encoder->crtc_id) : nullptr;
    if (!crtc || !crtc->mode_valid) {
        qCritical("mun-shell: display: requested %s, but no mode is set on %s %s", qPrintable(g_plan.requested),
                  kCard.data(), output.c_str());
    } else {
        const Timing applied = timingOf(crtc->mode);
        // An exact request must come out exact; the player's mode by size;
        // the display's own mode is whatever Qt took.
        bool same = true;
        if (g_requested)
            same = sameTiming(*g_requested, applied);
        else if (g_plan.active != QStringLiteral("auto"))
            same = g_plan.active == QStringLiteral("%1x%2").arg(applied.hdisplay).arg(applied.vdisplay);
        const QString line = QStringLiteral("display: requested %1, applied %2 on %3 %4")
                                 .arg(g_plan.requested, text(modeName(applied)), text(std::string(kCard)), text(output));
        if (same)
            qInfo("mun-shell: %s", qPrintable(line));
        else
            qCritical("mun-shell: %s, which is not the mode requested", qPrintable(line));
    }
    if (crtc)
        drmModeFreeCrtc(crtc);
    if (encoder)
        drmModeFreeEncoder(encoder);
    if (connector)
        drmModeFreeConnector(connector);
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
