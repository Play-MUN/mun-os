#include "memory.h"

#include <QTimer>

#if defined(__GLIBC__)
#include <malloc.h>
#endif

Memory::Memory(QObject *parent) : QObject(parent) {}

void Memory::settle()
{
    if (m_settled)
        return;
    m_settled = true;
#if defined(__GLIBC__)
    // Setting a threshold also turns glibc's raising of it off.
    mallopt(M_MMAP_THRESHOLD, kThreshold);
    mallopt(M_TRIM_THRESHOLD, kThreshold);
    QTimer::singleShot(kTrimDelay, this, [] {
        // Again, in case a free on another thread raised them since.
        mallopt(M_MMAP_THRESHOLD, kThreshold);
        mallopt(M_TRIM_THRESHOLD, kThreshold);
        malloc_trim(0);
    });
#endif
    emit settledChanged();
}
