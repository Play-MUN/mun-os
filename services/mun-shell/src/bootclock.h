// BootClock gives QML the start-up's clock (src/startupclock.h) on the
// monotonic clock, and writes each change of clock to the journal
// ("mun-shell: start-up: ..."). qml/BootLayer.qml calls tick() on every
// frame while the start-up runs. GUI thread only.
#pragma once

#include "startupclock.h"

#include <QObject>
#include <QtQml/qqmlregistration.h>

class BootClock : public QObject {
    Q_OBJECT
    QML_ELEMENT
    // The picture keeps time with the sound (false: it goes on alone).
    Q_PROPERTY(bool following READ following NOTIFY changed)

public:
    explicit BootClock(QObject *parent = nullptr) : QObject(parent) {}

    // The start-up begins: with its sound, or alone (sounds off).
    // `recoveries` is SystemSounds' count of recoveries so far.
    Q_INVOKABLE void begin(bool withSound, int recoveries);
    // One frame: what has been heard of the sound (NaN while not playing),
    // the device's recoveries. Returns t, the picture's time in seconds.
    Q_INVOKABLE double tick(double heard, int recoveries);
    // The sound's first samples went to the device.
    Q_INVOKABLE void started();
    // The sound will not be heard.
    Q_INVOKABLE void dropped();
    bool following() const { return m_clock.following(); }

signals:
    void changed();

private:
    static double now();
    void report(bool wasFollowing);

    StartupClock m_clock;
};
