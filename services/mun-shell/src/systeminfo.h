// SystemInfo exposes real facts of the running system to QML. Every value is
// read from Linux (/proc, /sys, /etc/os-release, /usr/lib/mun/release,
// statvfs, getifaddrs); nothing is a placeholder, and a fact that cannot be
// read is empty or zero rather than invented. Values are raw (numbers in
// bytes and seconds, identifiers), so the interface words them in its own
// language. refresh() reads everything again; refreshNetwork() only the
// network, cheap enough for a timer. GUI thread only.
#pragma once

#include <QObject>
#include <QStringList>
#include <QtQml/qqmlregistration.h>

class SystemInfo : public QObject {
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    // The system's identity, from /usr/lib/mun/release when a MUN OS image
    // build wrote it: "MUN OS 0.1.0-dev", its environment ("qemu-arm64") and
    // whether it is a published release. Without that file (services
    // installed outside an image build) the title is "MUN OS", fromImage is
    // false and the environment empty.
    Q_PROPERTY(QString osTitle READ osTitle NOTIFY changed)
    Q_PROPERTY(QString environment READ environment NOTIFY changed)
    Q_PROPERTY(bool release READ release NOTIFY changed)
    Q_PROPERTY(bool fromImage READ fromImage NOTIFY changed)
    Q_PROPERTY(QString osName READ osName NOTIFY changed)          // the base system's PRETTY_NAME
    Q_PROPERTY(QString hostname READ hostname NOTIFY changed)
    Q_PROPERTY(QString kernel READ kernel NOTIFY changed)          // "Linux 6.12.48 (aarch64)"
    Q_PROPERTY(QString machine READ machine NOTIFY changed)        // device-tree model or SMBIOS vendor and product
    Q_PROPERTY(int cpuCores READ cpuCores NOTIFY changed)
    Q_PROPERTY(QString cpuName READ cpuName NOTIFY changed)        // "implementer 0x61, part 0x000", or the architecture
    Q_PROPERTY(double memoryTotal READ memoryTotal NOTIFY changed) // bytes
    Q_PROPERTY(double memoryAvailable READ memoryAvailable NOTIFY changed)
    Q_PROPERTY(double storageTotal READ storageTotal NOTIFY changed) // bytes, of the system's root file system
    Q_PROPERTY(double storageFree READ storageFree NOTIFY changed)
    Q_PROPERTY(double uptime READ uptime NOTIFY changed)           // seconds
    Q_PROPERTY(QString timeZone READ timeZone NOTIFY changed)      // IANA identifier, "UTC" when none is set

    // The display: physical size, scale of the logical canvas and path.
    Q_PROPERTY(int displayWidth READ displayWidth NOTIFY changed)
    Q_PROPERTY(int displayHeight READ displayHeight NOTIFY changed)
    Q_PROPERTY(double displayScale READ displayScale NOTIFY changed)
    Q_PROPERTY(QString displayPath READ displayPath NOTIFY changed) // "linuxfb (DRM) · software renderer"
    Q_PROPERTY(QStringList inputDevices READ inputDevices NOTIFY changed)
    Q_PROPERTY(QString userName READ userName NOTIFY changed)
    Q_PROPERTY(int uid READ uid NOTIFY changed)

    // Network interfaces as the kernel sees them. Wired: a physical Ethernet
    // interface; connected when it has carrier and an IPv4 address. Wi-Fi: an
    // interface with a wireless extension; connected when up with an IPv4
    // address. The console has no network manager yet, so these only report.
    Q_PROPERTY(bool wiredPresent READ wiredPresent NOTIFY networkChanged)
    Q_PROPERTY(bool wiredConnected READ wiredConnected NOTIFY networkChanged)
    Q_PROPERTY(bool wifiPresent READ wifiPresent NOTIFY networkChanged)
    Q_PROPERTY(bool wifiConnected READ wifiConnected NOTIFY networkChanged)
    Q_PROPERTY(QStringList addresses READ addresses NOTIFY networkChanged) // "eth0 192.0.2.10"

    Q_PROPERTY(QString shellVersion READ shellVersion CONSTANT)
    Q_PROPERTY(QString buildId READ buildId CONSTANT)

public:
    explicit SystemInfo(QObject *parent = nullptr);

    Q_INVOKABLE void refresh();
    Q_INVOKABLE void refreshNetwork();

    QString osTitle() const { return m_osTitle; }
    QString environment() const { return m_environment; }
    bool release() const { return m_release; }
    bool fromImage() const { return m_fromImage; }
    QString osName() const { return m_osName; }
    QString hostname() const { return m_hostname; }
    QString kernel() const { return m_kernel; }
    QString machine() const { return m_machine; }
    int cpuCores() const { return m_cpuCores; }
    QString cpuName() const { return m_cpuName; }
    double memoryTotal() const { return m_memoryTotal; }
    double memoryAvailable() const { return m_memoryAvailable; }
    double storageTotal() const { return m_storageTotal; }
    double storageFree() const { return m_storageFree; }
    double uptime() const { return m_uptime; }
    QString timeZone() const { return m_timeZone; }
    int displayWidth() const { return m_displayWidth; }
    int displayHeight() const { return m_displayHeight; }
    double displayScale() const { return m_displayScale; }
    QString displayPath() const { return m_displayPath; }
    QStringList inputDevices() const { return m_inputDevices; }
    QString userName() const { return m_userName; }
    int uid() const { return m_uid; }
    bool wiredPresent() const { return m_wiredPresent; }
    bool wiredConnected() const { return m_wiredConnected; }
    bool wifiPresent() const { return m_wifiPresent; }
    bool wifiConnected() const { return m_wifiConnected; }
    QStringList addresses() const { return m_addresses; }
    QString shellVersion() const;
    QString buildId() const;

signals:
    void changed();
    void networkChanged();

private:
    QString m_osTitle, m_environment, m_osName, m_hostname, m_kernel, m_machine, m_cpuName, m_timeZone;
    QString m_displayPath, m_userName;
    bool m_release = false, m_fromImage = false;
    int m_cpuCores = 0, m_displayWidth = 0, m_displayHeight = 0, m_uid = -1;
    double m_memoryTotal = 0, m_memoryAvailable = 0, m_storageTotal = 0, m_storageFree = 0, m_uptime = 0;
    double m_displayScale = 1;
    QStringList m_inputDevices, m_addresses;
    bool m_wiredPresent = false, m_wiredConnected = false, m_wifiPresent = false, m_wifiConnected = false;
};
