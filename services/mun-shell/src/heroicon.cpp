#include "heroicon.h"

#include "blur.h"
#include "renderinfo.h"
#include "sceneclock.h"

#include <QLinearGradient>
#include <QList>
#include <QPainter>
#include <QPainterPath>
#include <QPainterPathStroker>
#include <QQuickWindow>
#include <QSGClipNode>
#include <QSGGeometryNode>
#include <QSGImageNode>
#include <QSGOpacityNode>
#include <QSGRendererInterface>
#include <QSGTransformNode>
#include <QSGVertexColorMaterial>
#include <QtMath>

#include <algorithm>
#include <array>
#include <cmath>
#include <iterator>
#include <limits>

namespace {

constexpr qreal kPi = 3.14159265358979323846;
constexpr qreal kLoadSeconds = 0.9;
constexpr qreal kLineWidth = 3;
// How far any object, its stroke and its glow reach from the centre, in the
// object's own units.
constexpr qreal kReach = 190;
// The crescent is painted again when it has filled this much more.
constexpr qreal kLitStep = 0.02;

QColor rgba(int r, int g, int b, qreal a)
{
    return QColor(r, g, b, std::lround(std::clamp<qreal>(a, 0, 1) * 255));
}

QPainterPath roundedRect(qreal x, qreal y, qreal w, qreal h, qreal r)
{
    QPainterPath path;
    path.addRoundedRect(QRectF(x, y, w, h), r, r);
    return path;
}

QPainterPath ellipse(qreal x, qreal y, qreal rx, qreal ry)
{
    QPainterPath path;
    path.addEllipse(QPointF(x, y), rx, ry);
    return path;
}

// A clockwise arc in the canvas's terms: radians from the positive x axis,
// growing clockwise on the screen.
QPainterPath arc(qreal x, qreal y, qreal r, qreal from, qreal to)
{
    const QRectF box(x - r, y - r, 2 * r, 2 * r);
    QPainterPath path;
    path.arcMoveTo(box, -qRadiansToDegrees(from));
    path.arcTo(box, -qRadiansToDegrees(from), -qRadiansToDegrees(to - from));
    return path;
}

// The logo's moon inside the card: the right half of a disc closed by half an
// ellipse, a thin crescent at 0 and the full disc at 1.
QPainterPath crescent(qreal x, qreal y, qreal r, qreal lit)
{
    constexpr int kSteps = 48;
    const qreal rx = std::max<qreal>(0.5, std::abs(1 - 2 * lit) * r);
    QPainterPath path;
    for (int i = 0; i <= kSteps; ++i) {
        const qreal th = -kPi / 2 + kPi * i / kSteps;
        const QPointF point(x + r * std::cos(th), y + r * std::sin(th));
        i ? path.lineTo(point) : path.moveTo(point);
    }
    // Back from the bottom to the top through the right (a crescent) or the
    // left (a gibbous moon).
    for (int i = 1; i <= kSteps; ++i) {
        const qreal th = lit < 0.5 ? kPi / 2 - kPi * i / kSteps : kPi / 2 + kPi * i / kSteps;
        path.lineTo(x + rx * std::cos(th), y + r * std::sin(th));
    }
    path.closeSubpath();
    return path;
}

QPainterPath gear(qreal inner, qreal outer, int teeth)
{
    QPainterPath path;
    const qreal half = kPi / (teeth * 2) * 0.55;
    for (int i = 0; i < teeth * 2; ++i) {
        const qreal a = i / qreal(teeth * 2) * 2 * kPi;
        const qreal r = i % 2 ? outer : inner;
        const QPointF first(std::cos(a - half) * r, std::sin(a - half) * r);
        i ? path.lineTo(first) : path.moveTo(first);
        path.lineTo(std::cos(a + half) * r, std::sin(a + half) * r);
    }
    path.closeSubpath();
    return path;
}

// One pass over an object's shapes, in the design's canvas terms: a face
// pass fills the main shapes (some always keep their own colours or a
// stroke), a wireframe pass strokes everything. A wireframe pass may give its
// strokes a glow (the canvas's shadowBlur): the glow of all of them is
// painted first and the strokes over it.
class Ink {
public:
    // `item` maps the item's coordinates to the painter's device and `scale`
    // is device pixels per item unit; glows are given in logical pixels and
    // multiplied by `sigmaScale` (the device pixels per logical pixel when
    // the item units are themselves device pixels, as in a cached layer).
    Ink(QPainter *painter, const QTransform &item, qreal scale, bool face, qreal sigmaScale = 1)
        : face(face), m_painter(painter), m_item(item), m_scale(scale), m_sigmaScale(sigmaScale) {}

    QPainter *painter() const { return m_painter; }

    void fillOrStroke(const QPainterPath &path) { face ? fillWith(path, fill) : stroke(path); }

    void fillWith(QPainterPath path, const QBrush &brush)
    {
        path.setFillRule(Qt::WindingFill);   // the canvas's nonzero rule
        m_painter->fillPath(path, brush);
    }

    void stroke(const QPainterPath &path)
    {
        const QPen pen(strokeColour, lineWidth, Qt::SolidLine, Qt::FlatCap, Qt::RoundJoin);
        if (glow.alpha() == 0) {
            m_painter->strokePath(path, pen);
            return;
        }
        QPainterPathStroker outline(pen);
        m_glowShape.addPath(toItem().map(outline.createStroke(path)));
        m_deferred.append({m_painter->worldTransform(), path, pen});
    }

    // A fill that casts its own glow, as the card's crescent does.
    void glowFill(const QPainterPath &path, const QColor &colour, const QColor &glowColour, qreal sigma)
    {
        QColor cast = glowColour;
        cast.setAlphaF(glowColour.alphaF() * colour.alphaF());
        paintGlow(toItem().map(path), cast, sigma);
        fillWith(path, colour);
    }

    // Paints the deferred glow and strokes of a wireframe pass.
    void finish()
    {
        if (m_deferred.isEmpty())
            return;
        QColor cast = glow;
        cast.setAlphaF(glow.alphaF() * strokeColour.alphaF());
        paintGlow(m_glowShape, cast, glowSigma);
        m_painter->save();
        for (const Deferred &d : std::as_const(m_deferred)) {
            m_painter->setWorldTransform(d.transform);
            m_painter->strokePath(d.path, d.pen);
        }
        m_painter->restore();
        m_deferred.clear();
        m_glowShape = {};
    }

    const bool face;
    QBrush fill;
    QColor strokeColour;
    qreal lineWidth = kLineWidth;
    qreal lit = 0.16;
    qreal time = 0;
    QColor glow = Qt::transparent;
    qreal glowSigma = 0;
    // The card's MUN Shape: its screen's image, its organic outline's shaping
    // (negative: MUN's card outline).
    const QImage *window = nullptr;
    qreal organic = -1;
    qreal reveal = 1;   // the window's share over MUN's screen and crescent

private:
    struct Deferred {
        QTransform transform;
        QPainterPath path;
        QPen pen;
    };

    // From the current drawing coordinates to the item's.
    QTransform toItem() const { return m_painter->worldTransform() * m_item.inverted(); }

    void paintGlow(const QPainterPath &itemShape, const QColor &colour, qreal sigma)
    {
        sigma *= m_sigmaScale;
        const qreal reach = sigma * 3;
        m_painter->save();
        m_painter->setWorldTransform(m_item);
        // Two pixels per sigma: the object repaints up to 60 times a second
        // while it loads, and a soft glow shows no difference.
        blur::paintBlurredPath(m_painter, itemShape.boundingRect().adjusted(-reach, -reach, reach, reach), itemShape,
                               colour, sigma, m_scale, 2);
        m_painter->restore();
    }

    QPainter *m_painter;
    QTransform m_item;
    qreal m_scale;
    qreal m_sigmaScale;
    QPainterPath m_glowShape;
    QList<Deferred> m_deferred;
};

// The card's organic outline: a superellipse of the card's size that rounds
// from nearly a rectangle towards an ellipse, and swells a little, as
// `morph` grows (0 to 1). Still: the same shape for the same morph.
QPainterPath organicCard(qreal morph)
{
    constexpr int kSteps = 120;
    const qreal exponent = 12 - 8.5 * morph;
    QPainterPath path;
    for (int i = 0; i <= kSteps; ++i) {
        const qreal th = 2 * kPi * i / kSteps;
        const qreal c = std::cos(th), s = std::sin(th);
        const qreal swell = 1 + 0.035 * morph * std::sin(3 * th + 0.6);
        const QPointF point(150 * swell * std::copysign(std::pow(std::abs(c), 2 / exponent), c),
                            105 * swell * std::copysign(std::pow(std::abs(s), 2 / exponent), s));
        i ? path.lineTo(point) : path.moveTo(point);
    }
    path.closeSubpath();
    return path;
}

void drawCard(Ink &ink)
{
    ink.fillOrStroke(ink.organic >= 0 ? organicCard(ink.organic) : roundedRect(-150, -105, 300, 210, 20));
    const QPainterPath screen = roundedRect(-128, -84, 236, 128, 12);
    const bool window = ink.window && !ink.window->isNull() && ink.reveal > 0;
    const bool whole = window && ink.reveal >= 1;
    if (ink.face && !whole) {
        QLinearGradient glass(0, -84, 0, 44);
        glass.setColorAt(0, rgba(34, 54, 82, 0.95));
        glass.setColorAt(1, rgba(8, 12, 20, 0.95));
        ink.fillWith(screen, glass);
    } else if (!ink.face) {
        ink.stroke(screen);
    }
    // The logo's crescent, until the card's image takes its place.
    if (!whole) {
        QPainter *p = ink.painter();
        p->save();
        p->setOpacity(p->opacity() * (window ? 1 - ink.reveal : 1));
        const QPainterPath moon = crescent(-10, -20, 46, ink.lit);
        if (ink.face)
            ink.glowFill(moon, rgba(236, 233, 227, 0.92), rgba(221, 233, 255, 0.6), 8);
        else
            ink.stroke(moon);
        p->restore();
    }
    if (ink.face && window) {
        // The card's own image fills the screen, cropped to its shape.
        QPainter *p = ink.painter();
        p->save();
        p->setOpacity(p->opacity() * ink.reveal);
        p->setClipPath(screen, Qt::IntersectClip);
        const QRectF box = screen.boundingRect();
        const QSizeF size = QSizeF(ink.window->size()).scaled(box.size(), Qt::KeepAspectRatioByExpanding);
        p->setRenderHint(QPainter::SmoothPixmapTransform);
        p->drawImage(QRectF(box.center() - QPointF(size.width() / 2, size.height() / 2), size), *ink.window);
        p->restore();
    }
    for (int i = 0; i < 7; ++i) {
        QPainterPath contact;
        contact.addRect(122, -70 + i * 20, 12, 12);
        if (ink.face)
            ink.fillWith(contact, QColor(0xC2, 0x7B, 0x48));
        else
            ink.stroke(contact);
    }
    QPainterPath edge;
    edge.moveTo(-128, 70);
    edge.lineTo(40, 70);
    ink.stroke(edge);
}

void drawGames(Ink &ink)
{
    // [turn in degrees, x, y] of three cards fanned out.
    constexpr std::array<std::array<qreal, 3>, 3> kFan{{{-16, -40, 24}, {-5, -15, 8}, {8, 14, -8}}};
    QPainter *p = ink.painter();
    for (int i = 0; i < 3; ++i) {
        p->save();
        p->translate(kFan[i][1], kFan[i][2]);
        p->rotate(kFan[i][0]);
        const QPainterPath body = roundedRect(-80, -110, 160, 220, 14);
        if (ink.face)
            ink.fillWith(body, rgba(30 + i * 10, 31 + i * 10, 36 + i * 10, 0.95));
        ink.stroke(body);
        ink.stroke(roundedRect(-64, -94, 128, 120, 9));
        p->restore();
    }
}

void drawSettings(Ink &ink)
{
    QPainter *p = ink.painter();
    p->save();
    p->rotate(qRadiansToDegrees(ink.time * 0.25));
    ink.fillOrStroke(gear(96, 124, 10));
    const QPainterPath hub = ellipse(0, 0, 44, 44);
    if (ink.face)
        ink.fillWith(hub, rgba(12, 13, 16, 0.9));
    ink.stroke(hub);
    p->restore();
}

void drawDisplay(Ink &ink)
{
    ink.fillOrStroke(roundedRect(-150, -100, 300, 180, 12));
    QPainterPath stand;
    stand.moveTo(-50, 115);
    stand.lineTo(50, 115);
    stand.moveTo(0, 80);
    stand.lineTo(0, 115);
    ink.stroke(stand);
}

void drawSound(Ink &ink)
{
    // A speaker and its waves.
    QPainterPath speaker;
    speaker.moveTo(-106, -32);
    speaker.lineTo(-66, -32);
    speaker.lineTo(-16, -82);
    speaker.lineTo(-16, 82);
    speaker.lineTo(-66, 32);
    speaker.lineTo(-106, 32);
    speaker.closeSubpath();
    ink.fillOrStroke(speaker);
    for (int i = 1; i <= 3; ++i)
        ink.stroke(arc(4, 0, 34 * i, -0.8, 0.8));
}

void drawAppearance(Ink &ink)
{
    // The logo's moon in its eclipse: how the console looks.
    const QPainterPath disc = ellipse(0, 0, 112, 112);
    if (ink.face)
        ink.fillWith(disc, rgba(12, 13, 16, 0.9));
    ink.stroke(disc);
    const QPainterPath moon = crescent(0, 0, 112, 0.32);
    if (ink.face)
        ink.glowFill(moon, rgba(236, 233, 227, 0.92), rgba(221, 233, 255, 0.6), 8);
    else
        ink.stroke(moon);
}

void drawLanguageAndTime(Ink &ink)
{
    // A globe, and a clock in front of it.
    ink.fillOrStroke(ellipse(-46, -18, 92, 92));
    ink.stroke(ellipse(-46, -18, 38, 92));
    QPainterPath equator;
    equator.moveTo(-138, -18);
    equator.lineTo(46, -18);
    ink.stroke(equator);
    const QPainterPath dial = ellipse(78, 58, 54, 54);
    if (ink.face)
        ink.fillWith(dial, rgba(12, 13, 16, 0.92));
    ink.stroke(dial);
    QPainterPath hands;
    hands.moveTo(78, 58);
    hands.lineTo(78, 24);
    hands.moveTo(78, 58);
    hands.lineTo(102, 72);
    ink.stroke(hands);
}

void drawNetwork(Ink &ink)
{
    for (int i = 1; i <= 4; ++i) {
        ink.lineWidth = ink.face ? 10 : kLineWidth;
        ink.stroke(arc(0, 70, 42 * i, -kPi * 0.78, -kPi * 0.22));
    }
    ink.lineWidth = kLineWidth;
    ink.fillOrStroke(ellipse(0, 70, 14, 14));
}

void drawSystem(Ink &ink)
{
    ink.fillOrStroke(roundedRect(-90, -90, 180, 180, 16));
    ink.stroke(roundedRect(-50, -50, 100, 100, 8));
    QPainterPath pins;
    for (int i = -3; i <= 3; ++i) {
        const qreal d = i * 24;
        pins.moveTo(d, -90);
        pins.lineTo(d, -118);
        pins.moveTo(d, 90);
        pins.lineTo(d, 118);
        pins.moveTo(-90, d);
        pins.lineTo(-118, d);
        pins.moveTo(90, d);
        pins.lineTo(118, d);
    }
    ink.stroke(pins);
}

// A band along a circle with round ends, clockwise from `from` to `to`
// (canvas radians): the power sign's ring.
QPainterPath roundBand(qreal radius, qreal half, qreal from, qreal to)
{
    const qreal outer = radius + half, inner = radius - half;
    const QRectF outerBox(-outer, -outer, 2 * outer, 2 * outer), innerBox(-inner, -inner, 2 * inner, 2 * inner);
    const QPointF end(radius * std::cos(to), radius * std::sin(to)), start(radius * std::cos(from), radius * std::sin(from));
    QPainterPath path;
    path.arcMoveTo(outerBox, -qRadiansToDegrees(from));
    path.arcTo(outerBox, -qRadiansToDegrees(from), -qRadiansToDegrees(to - from));
    path.arcTo(QRectF(end.x() - half, end.y() - half, 2 * half, 2 * half), -qRadiansToDegrees(to), -180);
    path.arcTo(innerBox, -qRadiansToDegrees(to), qRadiansToDegrees(to - from));
    path.arcTo(QRectF(start.x() - half, start.y() - half, 2 * half, 2 * half), -qRadiansToDegrees(from) + 180, -180);
    path.closeSubpath();
    return path;
}

void drawPower(Ink &ink)
{
    // A ring open at the top and a bar through the opening.
    constexpr qreal kGap = 0.62;
    ink.fillOrStroke(roundBand(105, 13, -kPi / 2 + kGap, 3 * kPi / 2 - kGap));
    ink.fillOrStroke(roundedRect(-13, -132, 26, 112, 13));
}

void drawShapes(const QString &key, Ink &ink)
{
    if (key == QLatin1String("card"))
        drawCard(ink);
    else if (key == QLatin1String("games"))
        drawGames(ink);
    else if (key == QLatin1String("settings"))
        drawSettings(ink);
    else if (key == QLatin1String("display"))
        drawDisplay(ink);
    else if (key == QLatin1String("sound"))
        drawSound(ink);
    else if (key == QLatin1String("appearance"))
        drawAppearance(ink);
    else if (key == QLatin1String("language"))
        drawLanguageAndTime(ink);
    else if (key == QLatin1String("network"))
        drawNetwork(ink);
    else if (key == QLatin1String("system"))
        drawSystem(ink);
    else if (key == QLatin1String("power"))
        drawPower(ink);
}

qreal smoothstep(qreal x)
{
    x = std::clamp<qreal>(x, 0, 1);
    return x * x * (3 - 2 * x);
}

} // namespace

HeroIcon::HeroIcon(QQuickItem *parent) : QQuickPaintedItem(parent)
{
    setAntialiasing(true);
    // One painting at a time, the newest object first in line.
    m_painter.setMaxThreadCount(1);
    connect(SceneClock::instance(), &SceneClock::tick, this, &HeroIcon::onTick);
    connect(SceneClock::instance(), &SceneClock::syncedChanged, this, &HeroIcon::updateDemand);
    updateDemand();
}

HeroIcon::~HeroIcon()
{
    // A painting in progress posts to this object: it ends before the
    // object does (and what it posts is dropped with the object).
    m_painter.clear();
    m_painter.waitForDone();
    SceneClock::instance()->demand(this, 0);
}

void HeroIcon::setIcon(const QString &icon)
{
    if (icon == m_icon)
        return;
    // On the graphics path an object whose layers have not come yet has
    // not shown: the one on screen stays the one that fades.
    if (!(onGpu() && m_progress == 0 && !ready(m_icon)))
        m_previous = m_icon;
    if (m_previous == icon)
        m_previous.clear();
    m_icon = icon;
    m_progress = 0;
    // Keep the layers of the two objects on screen only.
    for (auto it = m_layers.begin(); it != m_layers.end();)
        it = it.key() == m_icon || it.key() == m_previous ? std::next(it) : m_layers.erase(it);
    m_last = -1;
    request();
    updateDemand();
    update();
    emit iconChanged();
}

void HeroIcon::setLit(qreal lit)
{
    if (!std::isfinite(lit))
        return;
    lit = std::clamp<qreal>(lit, 0, 1);
    if (qFuzzyCompare(lit, m_litTarget))
        return;
    m_litTarget = lit;
    updateDemand();
    emit litChanged();
}

// A change of the card's Shape paints its layers again, once. The graphics
// path shows the card as it was until they come.
void HeroIcon::cardRestyled()
{
    if (onGpu())
        request();
    else
        m_layers.remove(QStringLiteral("card"));
    update();
    emit cardChanged();
}

void HeroIcon::setCardWindow(const QVariant &window)
{
    const QImage image = window.canConvert<QImage>() ? window.value<QImage>() : QImage();
    if (image.cacheKey() == m_window.cacheKey() && image.isNull() == m_window.isNull())
        return;
    m_window = image;
    cardRestyled();
}

void HeroIcon::setCardShape(const QString &shape)
{
    const QString valid = shape == QLatin1String("organic") ? shape : QStringLiteral("card");
    if (valid == m_cardShape)
        return;
    m_cardShape = valid;
    cardRestyled();
}

void HeroIcon::setMorph(qreal morph)
{
    morph = std::isfinite(morph) ? std::clamp<qreal>(morph, 0, 1) : 0;
    if (qFuzzyCompare(morph + 1, m_morph + 1))
        return;
    m_morph = morph;
    cardRestyled();
}

void HeroIcon::setCardGlow(const QColor &glow)
{
    if (glow == m_cardGlow)
        return;
    m_cardGlow = glow;
    cardRestyled();
}

void HeroIcon::setCardReveal(qreal reveal)
{
    reveal = std::isfinite(reveal) ? std::round(std::clamp<qreal>(reveal, 0, 1) * 10) / 10 : 1;
    if (qFuzzyCompare(reveal + 1, m_reveal + 1))
        return;
    m_reveal = reveal;
    cardRestyled();
}

void HeroIcon::setRunning(bool running)
{
    if (running == m_running)
        return;
    m_running = running;
    m_pace.setMoving(running);
    updateDemand();
    emit runningChanged();
}

bool HeroIcon::filling() const
{
    return std::abs(m_litTarget - m_lit) > 0.002;
}

int HeroIcon::rate() const
{
    // On a synced clock (an accelerated renderer, the graphics path) the
    // gear's turn and the float are transforms: every frame.
    const bool everyFrame = SceneClock::instance()->synced();
    // A load waits for its layers on the graphics path.
    if (loading() && (!onGpu() || ready(m_icon)))
        return 60;
    if (!m_pace.still() && everyFrame)
        return SceneClock::kBaseRate;
    if (filling() || (!m_pace.still() && m_icon == QLatin1String("settings")))
        return 20;
    // The float moves at most 3.6 px/s: whole pixels at 5 frames/s miss none.
    return m_pace.still() ? 0 : 5;
}

void HeroIcon::updateDemand()
{
    SceneClock::instance()->demand(this, rate());
}

void HeroIcon::onTick(quint64 frame)
{
    const int current = rate();
    if (current == 0 || !SceneClock::due(frame, current))
        return;
    const qreal now = SceneClock::instance()->seconds();
    const qreal dt = m_last >= 0 ? std::min<qreal>(0.1, now - m_last) : 1.0 / current;
    m_last = now;

    const bool gpu = onGpu();
    bool repaint = false;
    if (loading() && (!gpu || ready(m_icon))) {
        m_progress = std::min<qreal>(1, m_progress + dt / kLoadSeconds);
        repaint = true;
    }
    if (filling()) {
        m_lit += (m_litTarget - m_lit) * std::min<qreal>(1, dt * 1.8);
        if (!filling())
            m_lit = m_litTarget;
        // The graphics path shows the newest layers the painting thread
        // has made, and asks for the next.
        if (gpu)
            request();
        else
            repaint = repaint || m_icon == QLatin1String("card") || m_previous == QLatin1String("card");
    }
    if (!m_pace.still()) {
        m_time += m_pace.advance(dt);
        repaint = repaint || m_icon == QLatin1String("settings");
        const qreal bob = std::sin(m_time * 0.6) * 6;
        const qreal shown = SceneClock::instance()->synced() ? bob : qreal(std::lround(bob));
        if (std::abs(shown - m_bob) > 0.001) {
            m_bob = shown;
            emit bobChanged();
        }
    }
    if (repaint)
        update();
    if (rate() != current)
        updateDemand();
    // A stopped clock restarts from one frame's worth, not the pause.
    if (rate() == 0)
        m_last = -1;
}

HeroIcon::Look HeroIcon::look() const
{
    return {m_window, m_cardShape, m_morph, m_cardGlow, m_reveal, m_lit};
}

bool HeroIcon::onGpu() const
{
    return RenderInfo::instance()->rhi();
}

// What the card's layers show besides its crescent.
quint64 HeroIcon::styleKey() const
{
    return qHashMulti(0, m_window.isNull() ? 0 : m_window.cacheKey(), m_cardShape, m_morph, m_cardGlow.rgba(),
                      m_cardGlow.isValid(), m_reveal);
}

bool HeroIcon::ready(const QString &key) const
{
    const auto it = m_layers.constFind(key);
    return it != m_layers.cend() && it->scale > 0;
}

bool HeroIcon::stale(const QString &key, qreal scale) const
{
    const auto it = m_layers.constFind(key);
    if (it == m_layers.cend() || it->scale == 0 || !qFuzzyCompare(it->scale, scale))
        return true;
    if (key != QLatin1String("card"))
        return false;
    // The software path's rule for the crescent (layersFor).
    return it->look != styleKey() || std::abs(it->lit - m_lit) >= kLitStep || (!filling() && it->lit != m_lit);
}

void HeroIcon::request()
{
    if (!onGpu() || !window())
        return;
    const qreal scale = window()->effectiveDevicePixelRatio();
    // The object in focus first; the one fading out while it shows.
    for (const QString &key : {m_icon, m_previous}) {
        if (key.isEmpty() || m_asked.contains(key) || !stale(key, scale)
            || (key == m_previous && (m_progress >= 0.5 || key == m_icon)))
            continue;
        m_asked.insert(key, true);
        const Look look = this->look();
        const quint64 style = key == QLatin1String("card") ? styleKey() : 0;
        const QSizeF size = this->size();
        m_painter.start([this, key, look, scale, size, style] {
            QElapsedTimer took;
            took.start();
            Layers layers = paintLayers(key, look, scale, size, true);
            layers.look = style;
            const qint64 ms = took.elapsed();
            QMetaObject::invokeMethod(this, [this, key, layers, ms] { painted(key, layers, ms); }, Qt::QueuedConnection);
        });
    }
}

void HeroIcon::painted(const QString &key, const Layers &layers, qint64 ms)
{
    m_asked.remove(key);
    static const bool timing = qEnvironmentVariableIsSet("MUN_SHELL_TIMING");
    if (timing)
        qInfo("mun-shell: object %s layers painted in %lld ms (painting thread)", qPrintable(key), qlonglong(ms));
    // Kept only while the object shows.
    if (key == m_icon || (key == m_previous && m_progress < 0.5)) {
        m_layers.insert(key, layers);
        // A load waiting for them starts.
        updateDemand();
        update();
    }
    // Whatever moved on meanwhile (another object, the crescent).
    request();
}

void HeroIcon::paint(QPainter *painter)
{
    painter->setRenderHint(QPainter::Antialiasing);
    if (!m_previous.isEmpty() && m_progress < 0.5)
        drawObject(painter, m_previous, 1, 1 - m_progress * 2);
    drawObject(painter, m_icon, m_progress, 1);
}

// The object stands at the item's centre, leans a little into the scene and
// is drawn 1.35 times the size of its outline.
static void enterObjectFrame(QPainter *painter, qreal width, qreal height)
{
    painter->translate(width / 2, height / 2);
    painter->setTransform(QTransform(0.95, -0.05, 0.04, 1, 0, 0), true);
    painter->scale(1.35, 1.35);
}


const HeroIcon::Layers &HeroIcon::layersFor(const QString &key, qreal scale)
{
    Layers &layers = m_layers[key];
    const bool crescent = key == QLatin1String("card");
    const bool stale = layers.scale == 0 || !qFuzzyCompare(layers.scale, scale)
                       || (crescent && (std::abs(layers.lit - m_lit) >= kLitStep || (!filling() && layers.lit != m_lit)));
    if (!stale)
        return layers;
    QElapsedTimer took;
    took.start();
    layers = paintLayers(key, look(), scale, size(), false);
    // The laboratory times this (MUN_SHELL_TIMING): it runs where frames are made.
    static const bool timing = qEnvironmentVariableIsSet("MUN_SHELL_TIMING");
    if (timing && !layers.turning)
        qInfo("mun-shell: object %s layers painted in %lld ms", qPrintable(key), qlonglong(took.elapsed()));
    return layers;
}

// `parts` drawn over one another as one layer, in the device pixels they
// were painted in (`ratio` per logical pixel): the same picture as painting
// them all at once. Their origins share a fraction (one reference), so each
// lands on whole pixels of the result.
static HeroIcon::Layer composed(std::initializer_list<const HeroIcon::Layer *> parts, qreal ratio)
{
    QPointF first(std::numeric_limits<qreal>::max(), std::numeric_limits<qreal>::max());
    QPointF last(std::numeric_limits<qreal>::lowest(), std::numeric_limits<qreal>::lowest());
    for (const HeroIcon::Layer *layer : parts) {
        if (layer->image.isNull())
            continue;
        first = QPointF(std::min(first.x(), layer->origin.x()), std::min(first.y(), layer->origin.y()));
        last = QPointF(std::max(last.x(), layer->origin.x() + layer->size.width()),
                       std::max(last.y(), layer->origin.y() + layer->size.height()));
    }
    if (last.x() <= first.x())
        return {};
    QImage image(QSize(int(std::lround(last.x() - first.x())), int(std::lround(last.y() - first.y()))),
                 QImage::Format_ARGB32_Premultiplied);
    image.fill(Qt::transparent);
    QPainter p(&image);
    for (const HeroIcon::Layer *layer : parts) {
        if (layer->image.isNull())
            continue;
        QImage plain = layer->image;
        plain.setDevicePixelRatio(1);
        const QPointF at = layer->origin - first;
        p.drawImage(QPoint(int(std::lround(at.x())), int(std::lround(at.y()))), plain);
    }
    p.end();
    image.setDevicePixelRatio(ratio);
    return {image, first, image.size(), image.cacheKey()};
}

HeroIcon::Layers HeroIcon::paintLayers(const QString &key, const Look &look, qreal scale, const QSizeF &size,
                                       bool composite)
{
    Layers layers;
    layers.scale = scale;
    layers.lit = look.lit;
    layers.turning = key == QLatin1String("settings");

    if (layers.turning) {
        // In the object's own frame, unleaned: a frame turns and leans them.
        // The software path draws the shadow as vectors (fills only, cheap),
        // so it keeps its offset while the gear turns; the graphics path
        // turns a layer of it too.
        const int side = static_cast<int>(std::ceil(2 * kReach * 1.35 * scale));
        auto render = [&](int passes) {
            QImage image(side, side, QImage::Format_ARGB32_Premultiplied);
            image.fill(Qt::transparent);
            QPainter p(&image);
            p.setRenderHint(QPainter::Antialiasing);
            p.translate(side / 2.0, side / 2.0);
            p.scale(1.35 * scale, 1.35 * scale);
            // The image's own pixels are its item units: one device pixel each.
            drawLayers(&p, QTransform(), 1, scale, key, look, 1, 1, 0, passes, false);
            p.end();
            return cropped(image, QPointF(side / 2.0, side / 2.0), 1);
        };
        layers.wire = render(Wire);
        layers.face = render(Face);
        if (composite) {
            layers.shadow = render(Shadow);
            layers.loaded = composed({&layers.wire, &layers.face}, 1);
        } else {
            layers.loaded = render(Wire | Face);
        }
        return layers;
    }

    // As the item shows them, leaned, at device resolution: a frame copies
    // them without transforming a pixel.
    const QSize device(static_cast<int>(std::ceil(size.width() * scale)), static_cast<int>(std::ceil(size.height() * scale)));
    auto render = [&](int passes) {
        QImage image(device, QImage::Format_ARGB32_Premultiplied);
        image.fill(Qt::transparent);
        QPainter p(&image);
        p.setRenderHint(QPainter::Antialiasing);
        p.scale(scale, scale);
        enterObjectFrame(&p, size.width(), size.height());
        drawLayers(&p, QTransform::fromScale(scale, scale), scale, 1, key, look, 1, 1, 0, passes, true);
        p.end();
        return cropped(image, QPointF(0, 0), scale);
    };
    layers.shadow = render(Shadow);
    layers.wire = render(Wire);
    layers.face = render(Face);
    // The graphics path composes the three it already has (its glow is the
    // costly part); the software path paints it whole, as it always has.
    layers.loaded = composite ? composed({&layers.shadow, &layers.wire, &layers.face}, scale)
                              : render(Shadow | Wire | Face);
    return layers;
}

HeroIcon::Layer HeroIcon::cropped(const QImage &image, const QPointF &reference, qreal ratio)
{
    int left = image.width(), top = image.height(), right = -1, bottom = -1;
    for (int y = 0; y < image.height(); ++y) {
        const auto *line = reinterpret_cast<const quint32 *>(image.constScanLine(y));
        for (int x = 0; x < image.width(); ++x) {
            if (qAlpha(line[x]) == 0)
                continue;
            left = std::min(left, x);
            right = std::max(right, x);
            top = std::min(top, y);
            bottom = std::max(bottom, y);
        }
    }
    if (right < 0)
        return {};
    QImage shown = image.copy(QRect(QPoint(left, top), QPoint(right, bottom)));
    shown.setDevicePixelRatio(ratio);
    return {shown, QPointF(left, top) - reference, shown.size(), shown.cacheKey()};
}

// The load's sweep: the part of the object's frame the wireframe has reached.
static QPainterPath reached(qreal sweep)
{
    QPainterPath path;
    path.moveTo(0, 0);
    path.arcTo(QRectF(-400, -400, 800, 800), 90, -360 * sweep);
    path.closeSubpath();
    return path;
}

void HeroIcon::drawObject(QPainter *painter, const QString &key, qreal progress, qreal alpha)
{
    const qreal scale = blur::deviceScale(painter, width());
    const Layers &layers = layersFor(key, scale);
    const QTransform item = painter->worldTransform();
    const qreal sweep = std::min<qreal>(1, progress * 1.25);
    const qreal face = smoothstep((progress - 0.5) / 0.5);

    painter->save();
    painter->setRenderHint(QPainter::SmoothPixmapTransform);
    if (!layers.turning) {
        auto copy = [&](const Layer &layer, qreal opacity) {
            if (layer.image.isNull())
                return;
            painter->setOpacity(opacity);
            painter->drawImage(layer.origin / scale, layer.image);
        };
        if (progress >= 1) {
            copy(layers.loaded, alpha);
        } else {
            // Its thickness, then the wireframe as far as the sweep has
            // reached, then the face over the second half of the load.
            copy(layers.shadow, alpha * std::min<qreal>(1, progress * 1.6));
            painter->save();
            if (sweep < 1) {
                enterObjectFrame(painter, width(), height());
                painter->setClipPath(reached(sweep), Qt::IntersectClip);
                painter->setWorldTransform(item);
            }
            copy(layers.wire, alpha);
            painter->restore();
            if (face > 0)
                copy(layers.face, alpha * face);
        }
    } else {
        // The gear turns inside the load's clip, as in the design.
        const qreal turn = qRadiansToDegrees(m_time * 0.25);
        const qreal unit = 1 / (1.35 * scale);
        auto place = [&](const Layer &layer, qreal opacity) {
            if (layer.image.isNull())
                return;
            painter->save();
            painter->setOpacity(opacity);
            painter->rotate(turn);
            painter->scale(unit, unit);
            painter->drawImage(layer.origin, layer.image);
            painter->restore();
        };
        enterObjectFrame(painter, width(), height());
        drawLayers(painter, item, scale, 1, key, look(), progress, alpha, m_time, Shadow, true);
        if (progress >= 1) {
            place(layers.loaded, alpha);
        } else {
            painter->save();
            if (sweep < 1)
                painter->setClipPath(reached(sweep), Qt::IntersectClip);
            place(layers.wire, alpha);
            painter->restore();
            if (face > 0)
                place(layers.face, alpha * face);
        }
        painter->setWorldTransform(item);
    }

    // The sweep's copper line, fading as it completes.
    if (progress < 0.8) {
        enterObjectFrame(painter, width(), height());
        const qreal a = -kPi / 2 + 2 * kPi * sweep;
        painter->setOpacity(1);
        painter->setPen(QPen(rgba(227, 154, 99, 0.8 * (1 - progress / 0.8)), 2, Qt::SolidLine, Qt::FlatCap));
        painter->drawLine(QPointF(0, 0), QPointF(std::cos(a) * 200, std::sin(a) * 200));
    }
    painter->restore();
}

void HeroIcon::drawLayers(QPainter *painter, const QTransform &item, qreal scale, qreal sigmaScale, const QString &key,
                          const Look &look, qreal progress, qreal alpha, qreal time, int layers, bool offsetShadow)
{
    // The card's Shape, for the card only.
    const bool card = key == QLatin1String("card");
    const auto dress = [&](Ink &ink) {
        if (!card)
            return;
        ink.window = look.window.isNull() || look.reveal <= 0 ? nullptr : &look.window;
        ink.reveal = look.reveal;
        // The outline rounds into its Shape as the transition reaches it.
        ink.organic = look.cardShape == QLatin1String("organic") && look.reveal > 0 ? look.morph * look.reveal : -1;
    };
    if (layers & Shadow) {
        painter->save();
        if (offsetShadow)
            painter->translate(10, 9);
        painter->setOpacity(alpha * std::min<qreal>(1, progress * 1.6) * 0.8);
        Ink ink(painter, item, scale, true, sigmaScale);
        ink.fill = rgba(6, 10, 17, 0.92);
        ink.strokeColour = rgba(6, 10, 17, 0.92);
        ink.lit = look.lit;
        ink.time = time;
        dress(ink);
        ink.window = nullptr;   // a shadow shows no image
        drawShapes(key, ink);
        painter->restore();
    }
    if (layers & Wire) {
        painter->save();
        painter->setOpacity(alpha);
        Ink wire(painter, item, scale, false, sigmaScale);
        wire.strokeColour = rgba(226, 223, 217, 0.85);
        // MUN's light, turning into the card's as its Shape arrives.
        const qreal share = card && look.cardGlow.isValid() ? look.reveal : 0;
        wire.glow = rgba(int(std::lround(221 + (look.cardGlow.red() - 221) * share)),
                         int(std::lround(233 + (look.cardGlow.green() - 233) * share)),
                         int(std::lround(255 + (look.cardGlow.blue() - 255) * share)), 0.45);
        wire.glowSigma = 7;
        wire.lit = look.lit;
        wire.time = time;
        dress(wire);
        drawShapes(key, wire);
        wire.finish();
        painter->restore();
    }
    if (layers & Face) {
        painter->save();
        painter->setOpacity(alpha * smoothstep((progress - 0.5) / 0.5));
        Ink ink(painter, item, scale, true, sigmaScale);
        QLinearGradient light(-150, -150, 150, 150);
        light.setColorAt(0, rgba(214, 211, 205, 0.95));
        light.setColorAt(1, rgba(128, 126, 122, 0.95));
        ink.fill = light;
        ink.strokeColour = rgba(236, 233, 227, 0.9);
        ink.lit = look.lit;
        ink.time = time;
        dress(ink);
        drawShapes(key, ink);
        painter->restore();
    }
}

// The graphics path's nodes: each object shown (the one fading out, then the
// one in focus) as its layers' textures under opacities, the wireframe
// through the load's sweep (a clip), then the copper line.
namespace {

// The object's frame in the item: enterObjectFrame()'s transform.
QTransform objectFrame(qreal width, qreal height)
{
    return QTransform::fromScale(1.35, 1.35) * QTransform(0.95, -0.05, 0.04, 1, 0, 0)
           * QTransform::fromTranslate(width / 2, height / 2);
}

// One layer: its texture under an opacity. The texture is made once per
// picture (Layer::id) and the layer's image let go then.
struct Part {
    QSGOpacityNode *opacity = nullptr;
    QSGImageNode *image = nullptr;
    qint64 id = 0;

    // False when the picture is needed again but its image was let go
    // (the scene graph lost its textures): the layers must be painted again.
    bool show(QQuickWindow *window, HeroIcon::Layer &layer, const QRectF &rect, qreal opacity)
    {
        this->opacity->setOpacity(layer.id ? opacity : 0);
        if (!layer.id || opacity <= 0)
            return true;
        if (id != layer.id) {
            if (layer.image.isNull())
                return false;
            if (!image) {
                image = window->createImageNode();
                image->setOwnsTexture(true);
                image->setFiltering(QSGTexture::Linear);
                this->opacity->appendChildNode(image);
            }
            image->setTexture(window->createTextureFromImage(layer.image));
            id = layer.id;
            layer.image = QImage();
        }
        image->setRect(rect);
        image->setSourceRect(QRectF(QPointF(0, 0), image->texture()->textureSize()));
        return true;
    }
};

class ObjectNode : public QSGNode {
public:
    ObjectNode()
    {
        // The shadow; the wireframe within the sweep (the clip in the
        // object's frame, the layer placed back from it); the face; the
        // whole loaded object.
        appendChildNode(m_shadowFrame);
        m_shadowFrame->appendChildNode(m_shadow.opacity = new QSGOpacityNode);
        appendChildNode(m_sweepFrame);
        m_sweepFrame->appendChildNode(m_sweep);
        m_sweep->appendChildNode(m_wireFrame);
        m_wireFrame->appendChildNode(m_wire.opacity = new QSGOpacityNode);
        appendChildNode(m_layerFrame);
        m_layerFrame->appendChildNode(m_face.opacity = new QSGOpacityNode);
        m_layerFrame->appendChildNode(m_loaded.opacity = new QSGOpacityNode);
        m_sweep->setGeometry(&m_sweepShape);
        m_sweep->setIsRectangular(false);
        m_sweepShape.setDrawingMode(QSGGeometry::DrawTriangles);
    }

    // False when the layers must be painted again (Part::show).
    bool show(QQuickWindow *window, const QSizeF &size, HeroIcon::Layers &layers, qreal progress, qreal alpha,
              qreal turn)
    {
        const qreal scale = layers.scale;
        const QTransform frame = objectFrame(size.width(), size.height());
        // Where the layers' pixels go: the item, or (the gear) its own frame
        // turned and leaned. The gear's shadow keeps its offset as it turns.
        QTransform place, shadowPlace;
        if (layers.turning) {
            const qreal unit = 1 / (1.35 * scale);
            const QTransform turned = QTransform::fromScale(unit, unit) * QTransform().rotate(turn);
            place = turned * frame;
            shadowPlace = turned * QTransform::fromTranslate(10, 9) * frame;
        }
        m_shadowFrame->setMatrix(QMatrix4x4(shadowPlace));
        m_layerFrame->setMatrix(QMatrix4x4(place));
        m_sweepFrame->setMatrix(QMatrix4x4(frame));
        m_wireFrame->setMatrix(QMatrix4x4(place * frame.inverted()));
        auto rect = [&](const HeroIcon::Layer &layer) {
            return layers.turning ? QRectF(layer.origin, QSizeF(layer.size))
                                  : QRectF(layer.origin / scale, QSizeF(layer.size) / scale);
        };

        const qreal sweep = std::min<qreal>(1, progress * 1.25);
        const qreal face = smoothstep((progress - 0.5) / 0.5);
        const bool loaded = progress >= 1;
        setSweep(sweep);
        // The gear's shadow is not in its `loaded`; a still object's is.
        const qreal shadow = layers.turning || !loaded ? alpha * std::min<qreal>(1, progress * 1.6) : 0;
        bool whole = m_shadow.show(window, layers.shadow, rect(layers.shadow), shadow);
        whole = m_wire.show(window, layers.wire, rect(layers.wire), loaded ? 0 : alpha) && whole;
        whole = m_face.show(window, layers.face, rect(layers.face), loaded ? 0 : alpha * face) && whole;
        whole = m_loaded.show(window, layers.loaded, rect(layers.loaded), loaded ? alpha : 0) && whole;
        return whole;
    }

private:
    // reached(): the part of the object's frame, from the top clockwise,
    // that the sweep has reached; all of it once it has gone round.
    void setSweep(qreal sweep)
    {
        if (qFuzzyCompare(sweep, m_shownSweep))
            return;
        m_shownSweep = sweep;
        const int segments = std::max(1, static_cast<int>(std::ceil(sweep * 64)));
        m_sweepShape.allocate(3 * segments);
        QSGGeometry::Point2D *v = m_sweepShape.vertexDataAsPoint2D();
        for (int i = 0; i < segments; ++i) {
            const qreal a0 = kPi * 2 * sweep * i / segments, a1 = kPi * 2 * sweep * (i + 1) / segments;
            v[3 * i].set(0, 0);
            v[3 * i + 1].set(float(400 * std::sin(a0)), float(-400 * std::cos(a0)));
            v[3 * i + 2].set(float(400 * std::sin(a1)), float(-400 * std::cos(a1)));
        }
        m_sweep->markDirty(QSGNode::DirtyGeometry);
    }

    QSGTransformNode *m_shadowFrame = new QSGTransformNode;
    QSGTransformNode *m_sweepFrame = new QSGTransformNode;
    QSGClipNode *m_sweep = new QSGClipNode;
    QSGTransformNode *m_wireFrame = new QSGTransformNode;
    QSGTransformNode *m_layerFrame = new QSGTransformNode;
    QSGGeometry m_sweepShape{QSGGeometry::defaultAttributes_Point2D(), 0};
    qreal m_shownSweep = -1;
    Part m_shadow, m_wire, m_face, m_loaded;
};

class HeroNode : public QSGNode {
public:
    HeroNode()
    {
        auto *line = new QSGGeometry(QSGGeometry::defaultAttributes_ColoredPoint2D(), 8, 18);
        line->setDrawingMode(QSGGeometry::DrawTriangles);
        quint16 *index = line->indexDataAsUShort();
        // Three strips across the line, between its four columns.
        for (int c = 0; c < 3; ++c) {
            const quint16 a = quint16(c), b = quint16(c + 1), e = quint16(c + 4), f = quint16(c + 5);
            for (const quint16 i : {a, b, e, e, b, f})
                *index++ = i;
        }
        m_copper->setGeometry(line);
        m_copper->setMaterial(new QSGVertexColorMaterial);
        m_copper->setFlags(QSGNode::OwnsGeometry | QSGNode::OwnsMaterial);
        m_copperFrame->appendChildNode(m_copperOpacity);
        m_copperOpacity->appendChildNode(m_copper);
        appendChildNode(m_copperFrame);
    }

    // The objects in `order` (back to front), each shown by `show`; the
    // others removed.
    template<typename Show>
    void objects(const QStringList &order, Show show)
    {
        for (auto it = m_objects.begin(); it != m_objects.end();) {
            if (order.contains(it.key())) {
                ++it;
                continue;
            }
            removeChildNode(it.value());
            delete it.value();
            it = m_objects.erase(it);
        }
        if (order != m_order) {
            // Back to front, the copper line over them.
            for (ObjectNode *node : std::as_const(m_objects))
                removeChildNode(node);
            removeChildNode(m_copperFrame);
            for (const QString &key : order) {
                ObjectNode *&node = m_objects[key];
                if (!node)
                    node = new ObjectNode;
                appendChildNode(node);
            }
            appendChildNode(m_copperFrame);
            m_order = order;
        }
        for (const QString &key : order)
            show(key, m_objects.value(key));
    }

    // The sweep's copper line in the object's frame, 2 units wide (its edges
    // soft over a device pixel), from the centre at `angle`; `alpha` 0
    // hides it.
    void copper(const QTransform &frame, qreal angle, qreal alpha, qreal pixel)
    {
        m_copperOpacity->setOpacity(alpha > 0 ? 1 : 0);
        if (alpha <= 0)
            return;
        m_copperFrame->setMatrix(QMatrix4x4(frame));
        const QPointF dir(std::cos(angle), std::sin(angle));
        const QPointF across(-dir.y(), dir.x());
        const qreal edge = pixel / 2;
        const std::array<qreal, 4> offsets{-1 - edge, -1 + edge, 1 - edge, 1 + edge};
        const std::array<qreal, 4> shares{0, 1, 1, 0};
        QSGGeometry::ColoredPoint2D *v = m_copper->geometry()->vertexDataAsColoredPoint2D();
        for (int end = 0; end < 2; ++end) {
            const QPointF at = dir * (end ? 200.0 : 0.0);
            for (int c = 0; c < 4; ++c) {
                const QPointF p = at + across * offsets[c];
                const qreal a = alpha * shares[c];
                v[end * 4 + c].set(float(p.x()), float(p.y()), uchar(std::lround(227 * a)), uchar(std::lround(154 * a)),
                                   uchar(std::lround(99 * a)), uchar(std::lround(255 * a)));
            }
        }
        m_copper->markDirty(QSGNode::DirtyGeometry);
    }

private:
    QHash<QString, ObjectNode *> m_objects;
    QStringList m_order;
    QSGTransformNode *m_copperFrame = new QSGTransformNode;
    QSGOpacityNode *m_copperOpacity = new QSGOpacityNode;
    QSGGeometryNode *m_copper = new QSGGeometryNode;
};

} // namespace

QSGNode *HeroIcon::updatePaintNode(QSGNode *old, UpdatePaintNodeData *data)
{
    if (!window() || !QSGRendererInterface::isApiRhiBased(window()->rendererInterface()->graphicsApi()))
        return QQuickPaintedItem::updatePaintNode(old, data);
    auto *node = static_cast<HeroNode *>(old);
    if (!node)
        node = new HeroNode;
    // Render thread, the GUI thread blocked: the item's state is read here,
    // and a layer's image let go once its texture has it.
    QStringList order;
    if (!m_previous.isEmpty() && m_progress < 0.5 && ready(m_previous))
        order << m_previous;
    if (ready(m_icon))
        order << m_icon;
    const qreal turn = qRadiansToDegrees(m_time * 0.25);
    bool whole = true;
    node->objects(order, [&](const QString &key, ObjectNode *object) {
        Layers &layers = m_layers[key];
        const bool current = key == m_icon;
        whole = object->show(window(), size(), layers, current ? m_progress : 1, current ? 1 : 1 - m_progress * 2,
                             turn) && whole;
        if (!whole)
            layers.scale = 0;
    });
    // The sweep's copper line, fading as it completes.
    const bool sweeping = ready(m_icon) && m_progress < 0.8;
    const qreal sweep = std::min<qreal>(1, m_progress * 1.25);
    node->copper(objectFrame(width(), height()), -kPi / 2 + 2 * kPi * sweep,
                 sweeping ? 0.8 * (1 - m_progress / 0.8) : 0, 1 / (1.35 * window()->effectiveDevicePixelRatio()));
    // Layers missing (the first object, a new scale) or lost: asked for on
    // the GUI thread.
    const qreal scale = window()->effectiveDevicePixelRatio();
    if (!whole || stale(m_icon, scale) || (!m_previous.isEmpty() && m_progress < 0.5 && stale(m_previous, scale)))
        QMetaObject::invokeMethod(this, &HeroIcon::request, Qt::QueuedConnection);
    return node;
}

// The scene graph let go of the nodes, and the textures with them: the
// layers whose images were let go are painted again when next shown.
void HeroIcon::releaseResources()
{
    QQuickPaintedItem::releaseResources();
    for (Layers &layers : m_layers) {
        if (layers.loaded.id && layers.loaded.image.isNull())
            layers.scale = 0;
    }
}
