// Memory gives the memory the shell frees back to the system (glibc's
// allocator; with another C library it does nothing).
//
// glibc gives a block of 128 KiB or more a mapping of its own, returned to
// the system when the block is freed, but each time it frees such a block it
// raises that threshold to the block's size (up to 32 MiB). From then on,
// large blocks come from the heap or from a thread's arena, which keep the
// memory after the blocks are freed. The shell's large blocks are images: a
// Game Card's MUN Shape world, prepared on a thread of its own, left tens of
// MiB in that thread's arena once the card was out, and the start-up's
// picture left its own in the heap once QML had let it go.
//
// settle() fixes both thresholds at 128 KiB (glibc's starting values): every
// image from then on is returned when freed, and the heap is trimmed above
// 128 KiB free. kTrimDelay later it also gives back what is free by then
// (malloc_trim), the start-up's picture included. It is called once the
// start-up is over, or at once when it does not play: the start-up makes
// and frees large images on every frame, which the raised threshold keeps
// cheap; with the thresholds fixed, every one of them would be mapped and
// its pages faulted in anew.
//
// GUI thread only. Another thread may free a large block at the same moment
// and raise the thresholds again; the cost is memory kept, never an error.
#pragma once

#include <QObject>
#include <QtQml/qqmlregistration.h>

class Memory : public QObject {
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON
    // settle() has run.
    Q_PROPERTY(bool settled READ settled NOTIFY settledChanged)

public:
    static constexpr int kThreshold = 128 * 1024;
    // Longer than the start-up layer's fade and QML's deferred deletion of
    // what it drew.
    static constexpr int kTrimDelay = 3000;   // ms

    explicit Memory(QObject *parent = nullptr);

    // Idempotent.
    Q_INVOKABLE void settle();
    bool settled() const { return m_settled; }

signals:
    void settledChanged();

private:
    bool m_settled = false;
};
