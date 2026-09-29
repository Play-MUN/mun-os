#include "systeminfo.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QGuiApplication>
#include <QHash>
#include <QRegularExpression>
#include <QScreen>
#include <QTimeZone>

#include <arpa/inet.h>
#include <ifaddrs.h>
#include <netinet/in.h>
#include <sys/statvfs.h>
#include <sys/utsname.h>
#include <unistd.h>

namespace {

QString readFile(const QString &path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text))
        return {};
    return QString::fromUtf8(file.read(64 * 1024));
}

QString readLine(const QString &path)
{
    return readFile(path).trimmed();
}

// /proc/meminfo values are in kB.
double memInfoBytes(const QString &meminfo, const QString &key)
{
    const QRegularExpression re(QStringLiteral("^%1:\\s+(\\d+)").arg(key), QRegularExpression::MultilineOption);
    const auto match = re.match(meminfo);
    return match.hasMatch() ? match.captured(1).toDouble() * 1024 : 0;
}

// IPv4 addresses by interface, loopback left out.
QHash<QString, QStringList> ipv4Addresses()
{
    QHash<QString, QStringList> addresses;
    ifaddrs *list = nullptr;
    if (getifaddrs(&list) != 0)
        return addresses;
    for (ifaddrs *it = list; it; it = it->ifa_next) {
        if (!it->ifa_addr || it->ifa_addr->sa_family != AF_INET)
            continue;
        const QString name = QString::fromLocal8Bit(it->ifa_name);
        if (name == QLatin1String("lo"))
            continue;
        char buffer[INET_ADDRSTRLEN] = {};
        const auto *in = reinterpret_cast<const sockaddr_in *>(it->ifa_addr);
        if (inet_ntop(AF_INET, &in->sin_addr, buffer, sizeof buffer))
            addresses[name] << QString::fromLatin1(buffer);
    }
    freeifaddrs(list);
    return addresses;
}

} // namespace

SystemInfo::SystemInfo(QObject *parent) : QObject(parent)
{
    refresh();
}

void SystemInfo::refresh()
{
    utsname uts{};
    if (uname(&uts) == 0) {
        m_hostname = QString::fromLocal8Bit(uts.nodename);
        m_kernel = QStringLiteral("%1 %2 (%3)").arg(QString::fromLocal8Bit(uts.sysname), QString::fromLocal8Bit(uts.release),
                                                     QString::fromLocal8Bit(uts.machine));
    }

    const QString osRelease = readFile(QStringLiteral("/etc/os-release"));
    const auto pretty = QRegularExpression(QStringLiteral("^PRETTY_NAME=\"?([^\"\\n]+)"), QRegularExpression::MultilineOption).match(osRelease);
    m_osName = pretty.hasMatch() ? pretty.captured(1) : QStringLiteral("Linux");

    // A MUN OS image names itself in /usr/lib/mun/release (written by its
    // build from os/inputs.json, os/mkosi): name, version, environment and
    // whether it is a published release.
    const QString release = readFile(QStringLiteral("/usr/lib/mun/release"));
    const auto field = [&release](const QString &key) {
        const auto m = QRegularExpression(QStringLiteral("^%1\\s*=\\s*\"?([^\"\\n]*)").arg(key), QRegularExpression::MultilineOption).match(release);
        return m.hasMatch() ? m.captured(1).trimmed() : QString();
    };
    const QString version = field(QStringLiteral("version"));
    m_fromImage = !version.isEmpty();
    const QString name = field(QStringLiteral("name")).isEmpty() ? QStringLiteral("MUN OS") : field(QStringLiteral("name"));
    m_osTitle = m_fromImage ? QStringLiteral("%1 %2").arg(name, version) : QStringLiteral("MUN OS");
    m_environment = m_fromImage ? field(QStringLiteral("environment")) : QString();
    m_release = m_fromImage && field(QStringLiteral("release")) == QLatin1String("true");

    // A physical ARM board reports its model through the device tree; a
    // UEFI/ACPI guest reports it through SMBIOS instead.
    QString model = readLine(QStringLiteral("/sys/firmware/devicetree/base/model"));
    model.remove(QChar(0));
    if (model.isEmpty())
        model = QStringLiteral("%1 %2").arg(readLine(QStringLiteral("/sys/class/dmi/id/sys_vendor")),
                                            readLine(QStringLiteral("/sys/class/dmi/id/product_name"))).trimmed();
    m_machine = model;

    m_cpuCores = static_cast<int>(sysconf(_SC_NPROCESSORS_ONLN));
    const QString cpuinfo = readFile(QStringLiteral("/proc/cpuinfo"));
    const auto impl = QRegularExpression(QStringLiteral("^CPU implementer\\s*:\\s*(0x[0-9a-f]+)"), QRegularExpression::MultilineOption).match(cpuinfo);
    const auto part = QRegularExpression(QStringLiteral("^CPU part\\s*:\\s*(0x[0-9a-f]+)"), QRegularExpression::MultilineOption).match(cpuinfo);
    m_cpuName = impl.hasMatch() && part.hasMatch() ? QStringLiteral("implementer %1, part %2").arg(impl.captured(1), part.captured(1))
                                                   : QString::fromLocal8Bit(uts.machine);

    const QString meminfo = readFile(QStringLiteral("/proc/meminfo"));
    m_memoryTotal = memInfoBytes(meminfo, QStringLiteral("MemTotal"));
    m_memoryAvailable = memInfoBytes(meminfo, QStringLiteral("MemAvailable"));

    struct statvfs fs{};
    if (statvfs("/", &fs) == 0) {
        m_storageTotal = static_cast<double>(fs.f_blocks) * fs.f_frsize;
        m_storageFree = static_cast<double>(fs.f_bavail) * fs.f_frsize;
    }

    m_uptime = readFile(QStringLiteral("/proc/uptime")).section(QLatin1Char(' '), 0, 0).toDouble();
    const QByteArray zone = QTimeZone::systemTimeZoneId();
    m_timeZone = zone.isEmpty() ? QStringLiteral("UTC") : QString::fromUtf8(zone);

    if (const QScreen *screen = QGuiApplication::primaryScreen()) {
        // Physical framebuffer size; the logical canvas is this divided by the scale.
        const QSize physical = screen->size() * screen->devicePixelRatio();
        m_displayWidth = physical.width();
        m_displayHeight = physical.height();
        m_displayScale = screen->devicePixelRatio();
        // linuxfb draws through DRM when QT_QPA_FB_DRM is set (the unit sets it), through /dev/fb0 otherwise.
        m_displayPath = QGuiApplication::platformName()
                        + (qEnvironmentVariableIntValue("QT_QPA_FB_DRM") ? QStringLiteral(" (DRM)") : QString())
                        + QStringLiteral(" · ") + qEnvironmentVariable("QT_QUICK_BACKEND", QStringLiteral("default"))
                        + QStringLiteral(" renderer");
    }

    m_inputDevices.clear();
    const QString devices = readFile(QStringLiteral("/proc/bus/input/devices"));
    static const QRegularExpression nameRe(QStringLiteral("^N: Name=\"([^\"]+)\""), QRegularExpression::MultilineOption);
    for (auto it = nameRe.globalMatch(devices); it.hasNext();)
        m_inputDevices << it.next().captured(1);

    m_userName = qEnvironmentVariable("USER");
    m_uid = static_cast<int>(getuid());

    emit changed();
    refreshNetwork();
}

void SystemInfo::refreshNetwork()
{
    const QHash<QString, QStringList> ipv4 = ipv4Addresses();
    bool wiredPresent = false, wiredConnected = false, wifiPresent = false, wifiConnected = false;
    QStringList addresses;
    const QDir net(QStringLiteral("/sys/class/net"));
    for (const QString &name : net.entryList(QDir::Dirs | QDir::NoDotAndDotDot, QDir::Name)) {
        if (name == QLatin1String("lo"))
            continue;
        const QString base = net.absoluteFilePath(name);
        const bool hasAddress = ipv4.contains(name);
        for (const QString &address : ipv4.value(name))
            addresses << name + QLatin1Char(' ') + address;
        if (QFileInfo::exists(base + QStringLiteral("/wireless")) || QFileInfo::exists(base + QStringLiteral("/phy80211"))) {
            wifiPresent = true;
            wifiConnected = wifiConnected || (readLine(base + QStringLiteral("/operstate")) == QLatin1String("up") && hasAddress);
            continue;
        }
        // Type 1 is ARPHRD_ETHER; a device link tells hardware from virtual
        // interfaces (bridges, veth, tunnels).
        if (readLine(base + QStringLiteral("/type")) != QLatin1String("1") || !QFileInfo::exists(base + QStringLiteral("/device")))
            continue;
        wiredPresent = true;
        wiredConnected = wiredConnected || (readLine(base + QStringLiteral("/carrier")) == QLatin1String("1") && hasAddress);
    }
    if (wiredPresent == m_wiredPresent && wiredConnected == m_wiredConnected && wifiPresent == m_wifiPresent
        && wifiConnected == m_wifiConnected && addresses == m_addresses)
        return;
    m_wiredPresent = wiredPresent;
    m_wiredConnected = wiredConnected;
    m_wifiPresent = wifiPresent;
    m_wifiConnected = wifiConnected;
    m_addresses = addresses;
    emit networkChanged();
}

QString SystemInfo::shellVersion() const
{
    return QStringLiteral(MUN_SHELL_VERSION);
}

QString SystemInfo::buildId() const
{
    return QStringLiteral(MUN_BUILD_ID);
}
