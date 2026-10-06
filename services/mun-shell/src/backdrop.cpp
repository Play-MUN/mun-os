#include "backdrop.h"

#include "hairline.h"

#include <QPainter>
#include <QPainterPath>
#include <QQuickWindow>
#include <QRadialGradient>
#include <QSGImageNode>
#include <QSGTexture>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <utility>

namespace {

constexpr qreal kTau = 6.283185307179586;
constexpr int kWaveRate = 20;

// The ambient light through the day: [hour, r, g, b, alpha], eased between
// keys. Cold moonlight at night, a pale dawn, grey-white day, a copper dusk.
constexpr std::array<std::array<qreal, 5>, 8> kGlowKeys{{
    {0, 150, 160, 185, .10},
    {5.5, 150, 160, 185, .10},
    {7.2, 180, 200, 232, .22},
    {10, 226, 223, 216, .20},
    {16.5, 226, 223, 216, .20},
    {19, 214, 146, 94, .26},
    {21.5, 150, 160, 185, .10},
    {24, 150, 160, 185, .10},
}};

std::array<qreal, 4> glowAt(qreal hour)
{
    for (size_t i = 0; i + 1 < kGlowKeys.size(); ++i) {
        const auto &a = kGlowKeys[i];
        const auto &b = kGlowKeys[i + 1];
        if (hour >= a[0] && hour <= b[0]) {
            qreal u = (hour - a[0]) / (b[0] - a[0]);
            u = u * u * (3 - 2 * u);
            return {a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u, a[3] + (b[3] - a[3]) * u, a[4] + (b[4] - a[4]) * u};
        }
    }
    return {kGlowKeys[0][1], kGlowKeys[0][2], kGlowKeys[0][3], kGlowKeys[0][4]};
}

QColor rgba(qreal r, qreal g, qreal b, qreal a)
{
    return QColor(std::lround(std::clamp<qreal>(r, 0, 255)), std::lround(std::clamp<qreal>(g, 0, 255)),
                  std::lround(std::clamp<qreal>(b, 0, 255)), std::lround(std::clamp<qreal>(a, 0, 1) * 255));
}

// The same colour for `hairline`, which takes the alpha apart.
QRgb opaque(qreal r, qreal g, qreal b)
{
    return qRgb(std::lround(std::clamp<qreal>(r, 0, 255)), std::lround(std::clamp<qreal>(g, 0, 255)),
                std::lround(std::clamp<qreal>(b, 0, 255)));
}

// A card's colour in place of one of MUN's (a glow, the ambient light):
// the card's hue at MUN's colour's luminance, so that the world is only
// tinted, never lighter or darker than MUN drew it, and every MUN text over
// it keeps the contrast it was designed with. Channels 0-255.
qreal toLinear(qreal c)
{
    c /= 255;
    return c <= 0.04045 ? c / 12.92 : std::pow((c + 0.055) / 1.055, 2.4);
}
qreal toEncoded(qreal c)
{
    c = std::clamp<qreal>(c, 0, 1);
    return 255 * (c <= 0.0031308 ? c * 12.92 : 1.055 * std::pow(c, 1 / 2.4) - 0.055);
}
std::array<qreal, 3> atLuminanceOf(const QColor &hue, qreal r, qreal g, qreal b)
{
    const qreal target = 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
    const qreal lr = toLinear(hue.red()), lg = toLinear(hue.green()), lb = toLinear(hue.blue());
    const qreal own = 0.2126 * lr + 0.7152 * lg + 0.0722 * lb;
    if (own <= 0)
        return {r, g, b};
    const qreal scale = target / own;   // a channel that would pass 1 is held there: at most the luminance
    return {toEncoded(lr * scale), toEncoded(lg * scale), toEncoded(lb * scale)};
}

// Whether the ambient light painted as `painted` would now look different.
bool glowDiffers(const std::array<qreal, 4> &now, const std::array<qreal, 4> &painted)
{
    return std::abs(now[0] - painted[0]) >= 1 || std::abs(now[1] - painted[1]) >= 1
           || std::abs(now[2] - painted[2]) >= 1 || std::abs(now[3] - painted[3]) >= 0.002;
}

// The canvas's pen: butt caps (a dash of 2 is 2 long) and mitred joins.
QPen canvasPen(const QBrush &brush, qreal width)
{
    return QPen(brush, width, Qt::SolidLine, Qt::FlatCap, Qt::MiterJoin);
}

// The stretch [from, to] of the ray origin + direction * r that lies inside
// `rect` (Liang-Barsky); empty when from >= to.
std::pair<qreal, qreal> visibleStretch(const QPointF &origin, const QPointF &direction, qreal from, qreal to, const QRectF &rect)
{
    auto clip = [&](qreal p, qreal q) {
        if (qFuzzyIsNull(p))
            return q >= 0;
        const qreal r = q / p;
        if (p < 0)
            from = std::max(from, r);
        else
            to = std::min(to, r);
        return true;
    };
    if (!clip(-direction.x(), origin.x() - rect.left()) || !clip(direction.x(), rect.right() - origin.x())
        || !clip(-direction.y(), origin.y() - rect.top()) || !clip(direction.y(), rect.bottom() - origin.y()))
        return {0, 0};
    return {from, to};
}

// A radial gradient from radius `inner` to `outer` around `centre`, the
// canvas's createRadialGradient(x, y, inner, x, y, outer).
QRadialGradient ring(const QPointF &centre, qreal inner, qreal outer)
{
    return QRadialGradient(centre, outer, centre, inner);
}

} // namespace

Backdrop::Backdrop(QQuickItem *parent) : QQuickItem(parent)
{
    setFlag(ItemHasContents);
    connect(SceneClock::instance(), &SceneClock::tick, this, &Backdrop::onTick);
    updateDemand();
}

Backdrop::~Backdrop()
{
    SceneClock::instance()->demand(this, 0);
}

void Backdrop::setOrb(const QPointF &orb)
{
    if (orb == m_orb)
        return;
    m_orb = orb;
    m_base = {};
    repaint();
    emit orbChanged();
}

void Backdrop::setHour(qreal hour)
{
    if (!std::isfinite(hour))
        return;
    hour = std::fmod(std::fmod(hour, 24) + 24, 24);
    if (qFuzzyCompare(hour, m_hour))
        return;
    m_hour = hour;
    // A still screen follows the light too, when the change would show.
    if (glowDiffers(glow(), m_baseGlow))
        repaint();
    emit hourChanged();
}

void Backdrop::setGlowColor(const QColor &colour)
{
    if (colour == m_glowColor)
        return;
    m_glowColor = colour;
    if (glowDiffers(glow(), m_baseGlow))
        repaint();
    emit glowColorChanged();
}

void Backdrop::setTint(const QColor &colour)
{
    if (colour == m_tint)
        return;
    m_tint = colour;
    repaint();
    emit tintChanged();
}

Backdrop::Rgba Backdrop::glow() const
{
    Rgba g = glowAt(m_hour);
    if (m_glowColor.isValid() && m_glowColor.alpha() > 0) {
        // The card's hue at the hour's colour's luminance and strength.
        const auto c = atLuminanceOf(m_glowColor, g[0], g[1], g[2]);
        g[0] = c[0];
        g[1] = c[1];
        g[2] = c[2];
    }
    return g;
}

void Backdrop::setRunning(bool running)
{
    if (running == m_running)
        return;
    m_running = running;
    m_pace.setMoving(running);
    updateDemand();
    emit runningChanged();
}

void Backdrop::setDriftRate(int rate)
{
    rate = std::max(0, rate);
    if (rate == m_driftRate)
        return;
    m_driftRate = rate;
    updateDemand();
    emit driftRateChanged();
}

void Backdrop::ripple()
{
    m_ripples.append(0);
    updateDemand();
}

int Backdrop::rate() const
{
    if (!m_ripples.isEmpty())
        return kWaveRate;
    return m_driftRate > 0 && !m_pace.still() ? m_driftRate : 0;
}

void Backdrop::updateDemand()
{
    SceneClock::instance()->demand(this, rate());
}

void Backdrop::onTick(quint64 frame)
{
    const int current = rate();
    if (current == 0 || !SceneClock::due(frame, current))
        return;
    // The first frame after a pause moves one frame's worth, not the pause.
    const qreal dt = m_clock.isValid() ? std::min<qreal>(0.1, m_clock.restart() / 1000.0) : 1.0 / current;
    if (!m_clock.isValid())
        m_clock.start();
    // The drift's own time, eased to a stop at rest: with driftRate 0 the
    // rings and spokes stay where they are, and a wave only brightens and
    // ripples them as it passes.
    if (m_driftRate > 0)
        m_time += m_pace.advance(dt);
    for (qreal &u : m_ripples)
        u += dt * 0.55;
    m_ripples.removeIf([](qreal u) { return u >= 1; });
    if (rate() != current)
        updateDemand();
    // A stopped clock restarts from one frame's worth, not the pause.
    if (rate() == 0)
        m_clock.invalidate();
    repaint();
}

void Backdrop::repaint()
{
    m_stale = true;
    update();
}

void Backdrop::geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry)
{
    QQuickItem::geometryChange(newGeometry, oldGeometry);
    if (newGeometry.size() != oldGeometry.size()) {
        m_base = {};
        repaint();
    }
}

qreal Backdrop::wave(qreal radius) const
{
    qreal brightness = 0;
    for (const qreal u : m_ripples) {
        const qreal front = 200 + u * 1500;
        brightness += std::exp(-std::pow((radius - front) / 130, 2)) * (1 - u) * 0.55;
    }
    return std::min<qreal>(1, brightness);
}

void Backdrop::paintBase(const QSize &deviceSize, qreal scale)
{
    const qreal w = width(), h = height();
    const Rgba g = glow();
    m_base = QImage(deviceSize, QImage::Format_RGB32);
    m_base.setDevicePixelRatio(scale);
    m_baseGlow = g;
    m_baseTint = m_tint;
    const bool tinted = m_tint.isValid() && m_tint.alpha() > 0;
    QPainter p(&m_base);
    p.setRenderHint(QPainter::Antialiasing);
    const QRectF all(0, 0, w, h);

    // radial-gradient(90% 80% at 100% 100%, ...): an ellipse around the
    // bottom-right corner, drawn as a circle squeezed vertically.
    const qreal rx = 0.9 * w, ry = 0.8 * h;
    QRadialGradient night(QPointF(w, h), rx);
    night.setColorAt(0, QColor(0x10, 0x18, 0x26));
    night.setColorAt(0.45, QColor(0x0C, 0x0E, 0x13));
    night.setColorAt(1, QColor(0x07, 0x08, 0x0A));
    QBrush nightBrush(night);
    QTransform squeeze;
    squeeze.translate(w, h);
    squeeze.scale(1, ry / rx);
    squeeze.translate(-w, -h);
    nightBrush.setTransform(squeeze);
    p.fillRect(all, nightBrush);

    // The two glows, MUN's blue and patina, or the card's tint in their place
    // at their luminance and strength.
    const auto tintBlue = atLuminanceOf(m_tint, 46, 78, 118);
    const auto tintPatina = atLuminanceOf(m_tint, 78, 127, 114);
    const Rgba blueColour = tinted ? Rgba{tintBlue[0], tintBlue[1], tintBlue[2], 1} : Rgba{46, 78, 118, 1};
    const Rgba patinaColour = tinted ? Rgba{tintPatina[0], tintPatina[1], tintPatina[2], 1} : Rgba{78, 127, 114, 1};
    QRadialGradient blue = ring(QPointF(w * 0.92, h * 0.95), 60, 1100);
    blue.setColorAt(0, rgba(blueColour[0], blueColour[1], blueColour[2], 0.30));
    blue.setColorAt(1, rgba(blueColour[0], blueColour[1], blueColour[2], 0));
    p.fillRect(all, blue);

    QRadialGradient patina = ring(m_orb + QPointF(-120, 320), 20, 700);
    patina.setColorAt(0, rgba(patinaColour[0], patinaColour[1], patinaColour[2], 0.14));
    patina.setColorAt(1, rgba(patinaColour[0], patinaColour[1], patinaColour[2], 0));
    p.fillRect(all, patina);

    QRadialGradient ambient = ring(m_orb, 40, 1400);
    ambient.setColorAt(0, rgba(g[0], g[1], g[2], g[3] * 1.1));
    ambient.setColorAt(0.5, rgba(g[0], g[1], g[2], g[3] * 0.22));
    ambient.setColorAt(1, rgba(0, 0, 0, 0));
    p.fillRect(all, ambient);
}

QSGNode *Backdrop::updatePaintNode(QSGNode *old, UpdatePaintNodeData *)
{
    auto *node = static_cast<QSGImageNode *>(old);
    const qreal w = width(), h = height();
    if (w <= 0 || h <= 0 || !window()) {
        delete node;
        return nullptr;
    }
    if (!node) {
        node = window()->createImageNode();
        node->setOwnsTexture(true);
        m_stale = true;
    }
    const qreal scale = window()->effectiveDevicePixelRatio();
    const QSize deviceSize(std::lround(w * scale), std::lround(h * scale));
    if (m_stale || !node->texture() || node->texture()->textureSize() != deviceSize) {
        if (m_base.size() != deviceSize || glowDiffers(glow(), m_baseGlow) || m_baseTint != m_tint)
            paintBase(deviceSize, scale);
        // The image the scene graph showed before last is free again: its
        // texture was deleted when the last one replaced it.
        QImage &frame = m_frames[m_nextFrame];
        m_nextFrame = 1 - m_nextFrame;
        if (frame.size() != deviceSize) {
            frame = QImage(deviceSize, QImage::Format_RGB32);
            frame.setDevicePixelRatio(scale);
        }
        paintFrame(frame, scale);
        node->setTexture(window()->createTextureFromImage(frame));
        m_stale = false;
    }
    node->setRect(boundingRect());
    node->setSourceRect(QRectF(QPointF(0, 0), node->texture()->textureSize()));
    return node;
}

void Backdrop::paintFrame(QImage &frame, qreal scale) const
{
    std::memcpy(frame.bits(), m_base.constBits(), static_cast<size_t>(m_base.sizeInBytes()));

    const qreal t = m_time;
    const QPointF o = m_orb;

    // Rings leave the orb at 14 px/s; a passing wave brightens them towards
    // patina and makes them ripple. The design widens a lit ring to 1.5 px;
    // a one-pixel line with that much more alpha shows the same light.
    for (int k = 0; k < 10; ++k) {
        const qreal r = 230 + k * 150 + std::fmod(t * 14, 150);
        const qreal b = wave(r);
        const qreal base = 0.08 * (1 - k / 10.0);
        if (b > 0.02) {
            const QRgb colour = opaque(218 - 91 * b, 215 - 36 * b, 209 - 46 * b);
            const qreal alpha = std::min<qreal>(1, (base + 0.12 * b) * (1 + 0.5 * b));
            QPointF previous;
            for (int s = 0; s <= 180; ++s) {
                const qreal th = s / 180.0 * 6.2832;
                const qreal rr = r + 5 * b * std::sin(th * 9 + t * 2);
                const QPointF point = (o + QPointF(std::cos(th) * rr, std::sin(th) * rr)) * scale;
                if (s)
                    hairline::line(frame, previous, point, colour, alpha, alpha);
                previous = point;
            }
        } else {
            hairline::circle(frame, o * scale, r * scale, opaque(218, 215, 209), base);
        }
    }

    // Spokes fade out from the orb, from 240 to 1800 px, and turn once in
    // about nine minutes; a wave lights a patina stretch of each as it passes
    // (1.5 px wide in the design, drawn as above).
    constexpr qreal kSpokeFrom = 240, kSpokeTo = 1800;
    auto spokeAlpha = [](qreal r) { return 0.10 * (1 - (r - kSpokeFrom) / (kSpokeTo - kSpokeFrom)); };
    const QRectF screen = boundingRect().adjusted(-1, -1, 1, 1);
    for (int k = 0; k < 36; ++k) {
        const qreal a = k / 36.0 * 6.283 + t * 0.012;
        const QPointF dir(std::cos(a), std::sin(a));
        const auto [from, to] = visibleStretch(o, dir, kSpokeFrom, kSpokeTo, screen);
        if (from < to)
            hairline::line(frame, (o + dir * from) * scale, (o + dir * to) * scale, opaque(218, 215, 209), spokeAlpha(from),
                           spokeAlpha(to));
        for (const qreal u : std::as_const(m_ripples)) {
            const qreal front = 200 + u * 1500;
            const qreal alpha = std::min<qreal>(1, 0.10 * (1 - u) * 1.5);
            hairline::line(frame, (o + dir * (front - 60)) * scale, (o + dir * (front + 60)) * scale, opaque(127, 179, 163),
                           alpha, alpha);
        }
    }

    QPainter painter(&frame);
    painter.setRenderHint(QPainter::Antialiasing);

    // Two dashed orbits, tilted, their dashes crawling at 8 and 13 px/s.
    for (int k = 0; k < 2; ++k) {
        const qreal rx = 330 + k * 70, ry = rx * 0.28;
        QPen pen = canvasPen(rgba(218, 215, 209, 0.14 - k * 0.04), 1);
        pen.setDashPattern({2, 10});
        pen.setDashOffset(-t * (8 + k * 5));
        painter.save();
        painter.translate(o);
        painter.rotate(qRadiansToDegrees(-0.35 + k * 0.4));
        painter.setPen(pen);
        // Clockwise from angle 0, as the canvas ellipse the dashes follow.
        QPainterPath orbit;
        constexpr int kSegments = 160;
        for (int s = 0; s <= kSegments; ++s) {
            const qreal th = s * 6.29 / kSegments;
            const QPointF point(std::cos(th) * rx, std::sin(th) * ry);
            s ? orbit.lineTo(point) : orbit.moveTo(point);
        }
        painter.drawPath(orbit);
        painter.restore();
    }

    // The floor: lines crowding towards the horizon at y 880, sliding down;
    // 1 px rectangles, which the raster engine fills faster than it strokes.
    const qreal w = width(), h = height();
    const qreal horizon = 880;
    for (int k = 0; k < 14; ++k) {
        const qreal u = (k + std::fmod(t * 0.18, 1)) / 14;
        const qreal y = horizon + (h - horizon) * u * u;
        painter.fillRect(QRectF(0, y - 0.5, w, 1), rgba(218, 215, 209, 0.08 * u));
    }
}
