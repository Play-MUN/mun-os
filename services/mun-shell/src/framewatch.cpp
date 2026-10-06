#include "framewatch.h"

#include <algorithm>

namespace {

qreal percentile(std::vector<qreal> values, qreal p)
{
    if (values.empty())
        return 0;
    std::sort(values.begin(), values.end());
    const size_t index = std::min(values.size() - 1, size_t(p * (values.size() - 1) + 0.5));
    return values[index];
}

} // namespace

FrameWatch::FrameWatch(QObject *parent) : QObject(parent)
{
    m_clock.start();
    m_windowTimer.setInterval(kWindowMs);
    connect(&m_windowTimer, &QTimer::timeout, this, &FrameWatch::judge);
    m_windowTimer.start();
    const QString path = qEnvironmentVariable("MUN_SHELL_TIMING");
    if (!path.isEmpty()) {
        m_log.setFileName(path);
        if (!m_log.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text))
            qWarning("mun-shell: timing log %s cannot be opened", qPrintable(path));
    }
}

void FrameWatch::setWindow(QQuickWindow *window)
{
    if (window == m_window)
        return;
    if (m_window)
        disconnect(m_window, nullptr, this, nullptr);
    m_window = window;
    if (m_window)
        connect(m_window, &QQuickWindow::frameSwapped, this, &FrameWatch::onFrame, Qt::DirectConnection);
    emit windowChanged();
}

void FrameWatch::setJudging(bool judging)
{
    if (judging == m_judging)
        return;
    m_judging = judging;
    m_intervals.clear();
    m_keys.clear();
    emit judgingChanged();
}

void FrameWatch::setAnimating(bool animating)
{
    if (animating == m_animating)
        return;
    m_animating = animating;
    emit animatingChanged();
}

void FrameWatch::keyPressed()
{
    const qint64 now = m_clock.elapsed();
    if (m_keyAt < 0)
        m_keyAt = now;
    m_activeUntil = now + kActiveMs;
}

void FrameWatch::mark(const QString &what)
{
    log(QStringLiteral("mark %1").arg(what));
}

void FrameWatch::onFrame()
{
    const qint64 now = m_clock.elapsed();
    if (m_lastFrame >= 0 && now - m_lastFrame < kIdleMs) {
        // A transition's frames, keys pressed during it included, are timed
        // apart: never judged.
        if (m_animating)
            m_transition.push_back(qreal(now - m_lastFrame));
        else if (now < m_activeUntil)
            m_intervals.push_back(qreal(now - m_lastFrame));
    }
    m_lastFrame = now;
    if (m_keyAt >= 0) {
        const qreal latency = qreal(now - m_keyAt);
        if (!m_animating)
            m_keys.push_back(latency);
        log(QStringLiteral("key %1%2").arg(latency, 0, 'f', 1).arg(m_animating ? QStringLiteral(" transition") : QString()));
        m_keyAt = -1;
    }
}

void FrameWatch::judge()
{
    const qreal p50 = percentile(m_intervals, 0.5), p95 = percentile(m_intervals, 0.95);
    const qreal worst = m_intervals.empty() ? 0 : *std::max_element(m_intervals.begin(), m_intervals.end());
    const qreal keys = percentile(m_keys, 0.5);
    if (!m_transition.empty())
        log(QStringLiteral("transition frames %1 p50 %2 p95 %3")
                .arg(m_transition.size())
                .arg(percentile(m_transition, 0.5), 0, 'f', 1)
                .arg(percentile(m_transition, 0.95), 0, 'f', 1));
    m_transition.clear();
    if (!m_intervals.empty() || !m_keys.empty())
        log(QStringLiteral("window frames %1 p50 %2 p95 %3 max %4 keys %5 median %6")
                .arg(m_intervals.size())
                .arg(p50, 0, 'f', 1)
                .arg(p95, 0, 'f', 1)
                .arg(worst, 0, 'f', 1)
                .arg(m_keys.size())
                .arg(keys, 0, 'f', 1));
    const qint64 now = m_clock.elapsed();
    if (m_judging && now >= m_calmUntil) {
        QString why;
        if (m_intervals.size() >= size_t(kMinFrames) && p95 > kFrameLimitMs)
            why = QStringLiteral("frames p95 %1 ms").arg(p95, 0, 'f', 1);
        else if (!m_keys.empty() && keys > kKeyLimitMs)
            why = QStringLiteral("key to frame %1 ms").arg(keys, 0, 'f', 1);
        m_strained = why.isEmpty() ? 0 : m_strained + 1;
        if (!why.isEmpty())
            log(QStringLiteral("strain %1 (%2 in a row)").arg(why).arg(m_strained));
        if (m_strained >= kStrainedWindows) {
            m_strained = 0;
            m_calmUntil = now + kCalmMs;
            log(QStringLiteral("strained %1").arg(why));
            emit strained(why);
        }
    }
    m_intervals.clear();
    m_keys.clear();
}

void FrameWatch::log(const QString &line)
{
    if (!m_log.isOpen())
        return;
    m_log.write(QStringLiteral("%1 %2\n").arg(m_clock.elapsed()).arg(line).toUtf8());
    m_log.flush();
}
