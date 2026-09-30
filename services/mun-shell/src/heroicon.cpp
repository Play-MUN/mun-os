#include "heroicon.h"

#include "blur.h"
#include "sceneclock.h"

#include <QLinearGradient>
#include <QList>
#include <QPainter>
#include <QPainterPath>
#include <QPainterPathStroker>
#include <QtMath>

#include <algorithm>
#include <array>
#include <cmath>
#include <iterator>

namespace {

constexpr qreal kPi = 3.14159265358979323846;
constexpr qreal kLoadSeconds = 0.9;
constexpr qreal kLineWidth = 3;

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
    const bool window = ink.window && !ink.window->isNull();
    if (ink.face && window) {
        // The card's own image fills the screen, cropped to its shape.
        QPainter *p = ink.painter();
        p->save();
        p->setClipPath(screen, Qt::IntersectClip);
        const QRectF box = screen.boundingRect();
        const QSizeF size = QSizeF(ink.window->size()).scaled(box.size(), Qt::KeepAspectRatioByExpanding);
        p->setRenderHint(QPainter::SmoothPixmapTransform);
        p->drawImage(QRectF(box.center() - QPointF(size.width() / 2, size.height() / 2), size), *ink.window);
        p->restore();
    } else if (ink.face) {
        QLinearGradient glass(0, -84, 0, 44);
        glass.setColorAt(0, rgba(34, 54, 82, 0.95));
        glass.setColorAt(1, rgba(8, 12, 20, 0.95));
        ink.fillWith(screen, glass);
    } else {
        ink.stroke(screen);
    }
    // The logo's crescent, unless the card's image takes its place.
    if (!window) {
        const QPainterPath moon = crescent(-10, -20, 46, ink.lit);
        if (ink.face)
            ink.glowFill(moon, rgba(236, 233, 227, 0.92), rgba(221, 233, 255, 0.6), 8);
        else
            ink.stroke(moon);
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

void drawPersonal(Ink &ink)
{
    ink.fillOrStroke(ellipse(0, -44, 50, 50));
    QPainterPath body;
    body.moveTo(-100, 110);
    body.cubicTo(-100, 20, 100, 20, 100, 110);
    body.closeSubpath();
    ink.fillOrStroke(body);
    // A globe: the language's sign.
    ink.stroke(ellipse(118, -80, 34, 34));
    ink.stroke(ellipse(118, -80, 14, 34));
    QPainterPath equator;
    equator.moveTo(84, -80);
    equator.lineTo(152, -80);
    ink.stroke(equator);
}

void drawPictureAndSound(Ink &ink)
{
    ink.fillOrStroke(roundedRect(-150, -95, 220, 150, 12));
    QPainterPath stand;
    stand.moveTo(-60, 80);
    stand.lineTo(-20, 80);
    stand.moveTo(-40, 55);
    stand.lineTo(-40, 80);
    ink.stroke(stand);
    for (int i = 1; i <= 3; ++i)
        ink.stroke(arc(90, -20, 22 * i, -0.8, 0.8));
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
    else if (key == QLatin1String("personal"))
        drawPersonal(ink);
    else if (key == QLatin1String("av"))
        drawPictureAndSound(ink);
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
    connect(SceneClock::instance(), &SceneClock::tick, this, &HeroIcon::onTick);
    updateDemand();
}

HeroIcon::~HeroIcon()
{
    SceneClock::instance()->demand(this, 0);
}

void HeroIcon::setIcon(const QString &icon)
{
    if (icon == m_icon)
        return;
    m_previous = m_icon;
    m_icon = icon;
    m_progress = 0;
    // Keep the layers of the two objects on screen only.
    for (auto it = m_layers.begin(); it != m_layers.end();)
        it = it.key() == m_icon || it.key() == m_previous ? std::next(it) : m_layers.erase(it);
    m_clock.invalidate();
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

// A change of the card's Shape paints its layers again, once.
void HeroIcon::cardRestyled()
{
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
    if (loading())
        return 60;
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
    const qreal dt = m_clock.isValid() ? std::min<qreal>(0.1, m_clock.restart() / 1000.0) : 1.0 / current;
    if (!m_clock.isValid())
        m_clock.start();

    bool repaint = false;
    if (loading()) {
        m_progress = std::min<qreal>(1, m_progress + dt / kLoadSeconds);
        repaint = true;
    }
    if (filling()) {
        m_lit += (m_litTarget - m_lit) * std::min<qreal>(1, dt * 1.8);
        if (!filling())
            m_lit = m_litTarget;
        repaint = repaint || m_icon == QLatin1String("card") || m_previous == QLatin1String("card");
    }
    if (!m_pace.still()) {
        m_time += m_pace.advance(dt);
        repaint = repaint || m_icon == QLatin1String("settings");
        const int bob = static_cast<int>(std::lround(std::sin(m_time * 0.6) * 6));
        if (bob != m_bob) {
            m_bob = bob;
            emit bobChanged();
        }
    }
    if (repaint)
        update();
    if (rate() != current)
        updateDemand();
    // A stopped clock restarts from one frame's worth, not the pause.
    if (rate() == 0)
        m_clock.invalidate();
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

// How far any object, its stroke and its glow reach from the centre, in the
// object's own units.
constexpr qreal kReach = 190;
// The crescent is painted again when it has filled this much more.
constexpr qreal kLitStep = 0.02;

const HeroIcon::Layers &HeroIcon::layersFor(const QString &key, qreal scale)
{
    Layers &layers = m_layers[key];
    const bool crescent = key == QLatin1String("card");
    const bool stale = layers.scale == 0 || !qFuzzyCompare(layers.scale, scale)
                       || (crescent && (std::abs(layers.lit - m_lit) >= kLitStep || (!filling() && layers.lit != m_lit)));
    if (!stale)
        return layers;
    layers.scale = scale;
    layers.lit = m_lit;
    layers.turning = key == QLatin1String("settings");

    if (layers.turning) {
        // In the object's own frame, unleaned: a frame turns and leans them.
        // The shadow is drawn as vectors (fills only, cheap), so it keeps its
        // offset while the gear turns.
        const int side = static_cast<int>(std::ceil(2 * kReach * 1.35 * scale));
        auto render = [&](int passes) {
            QImage image(side, side, QImage::Format_ARGB32_Premultiplied);
            image.fill(Qt::transparent);
            QPainter p(&image);
            p.setRenderHint(QPainter::Antialiasing);
            p.translate(side / 2.0, side / 2.0);
            p.scale(1.35 * scale, 1.35 * scale);
            // The image's own pixels are its item units: one device pixel each.
            drawLayers(&p, QTransform(), 1, scale, key, 1, 1, 0, passes, false);
            p.end();
            return cropped(image, QPointF(side / 2.0, side / 2.0), 1);
        };
        layers.shadow = {};
        layers.wire = render(Wire);
        layers.face = render(Face);
        layers.loaded = render(Wire | Face);
        return layers;
    }

    // As the item shows them, leaned, at device resolution: a frame copies
    // them without transforming a pixel.
    const QSize size(static_cast<int>(std::ceil(width() * scale)), static_cast<int>(std::ceil(height() * scale)));
    auto render = [&](int passes) {
        QImage image(size, QImage::Format_ARGB32_Premultiplied);
        image.fill(Qt::transparent);
        QPainter p(&image);
        p.setRenderHint(QPainter::Antialiasing);
        p.scale(scale, scale);
        enterObjectFrame(&p, width(), height());
        drawLayers(&p, QTransform::fromScale(scale, scale), scale, 1, key, 1, 1, 0, passes, true);
        p.end();
        return cropped(image, QPointF(0, 0), scale);
    };
    layers.shadow = render(Shadow);
    layers.wire = render(Wire);
    layers.face = render(Face);
    layers.loaded = render(Shadow | Wire | Face);
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
    return {shown, QPointF(left, top) - reference};
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
        drawLayers(painter, item, scale, 1, key, progress, alpha, m_time, Shadow, true);
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
                          qreal progress, qreal alpha, qreal time, int layers, bool offsetShadow) const
{
    // The card's Shape, for the card only.
    const bool card = key == QLatin1String("card");
    const auto dress = [&](Ink &ink) {
        if (!card)
            return;
        ink.window = m_window.isNull() ? nullptr : &m_window;
        ink.organic = m_cardShape == QLatin1String("organic") ? m_morph : -1;
    };
    if (layers & Shadow) {
        painter->save();
        if (offsetShadow)
            painter->translate(10, 9);
        painter->setOpacity(alpha * std::min<qreal>(1, progress * 1.6) * 0.8);
        Ink ink(painter, item, scale, true, sigmaScale);
        ink.fill = rgba(6, 10, 17, 0.92);
        ink.strokeColour = rgba(6, 10, 17, 0.92);
        ink.lit = m_lit;
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
        wire.glow = card && m_cardGlow.isValid() ? rgba(m_cardGlow.red(), m_cardGlow.green(), m_cardGlow.blue(), 0.45)
                                                 : rgba(221, 233, 255, 0.45);
        wire.glowSigma = 7;
        wire.lit = m_lit;
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
        ink.lit = m_lit;
        ink.time = time;
        dress(ink);
        drawShapes(key, ink);
        painter->restore();
    }
}
