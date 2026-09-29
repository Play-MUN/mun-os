#include "blur.h"

#include <QList>
#include <QPaintDevice>
#include <QPainter>
#include <QPainterPath>

#include <algorithm>
#include <cmath>

namespace {

// Box widths whose three passes approximate a Gaussian of `sigma`
// (W. M. Wells, "Efficient synthesis of Gaussian filters by cascaded uniform
// filters", 1986; the integer split of the widths follows the usual recipe).
QList<int> boxWidths(double sigma, int passes)
{
    const double ideal = std::sqrt(12.0 * sigma * sigma / passes + 1.0);
    int lower = static_cast<int>(std::floor(ideal));
    if (lower % 2 == 0)
        --lower;
    const int upper = lower + 2;
    const double m = (12.0 * sigma * sigma - passes * lower * lower - 4.0 * passes * lower - 3.0 * passes)
                     / (-4.0 * lower - 4.0);
    const int lowerCount = static_cast<int>(std::lround(m));
    QList<int> widths;
    for (int i = 0; i < passes; ++i)
        widths << (i < lowerCount ? lower : upper);
    return widths;
}

// One box pass along a line of `count` pixels spaced `stride` apart. Outside
// the image counts as transparent, which is what a shadow's margin is.
void boxLine(quint32 *line, int count, int stride, int radius, quint32 *scratch)
{
    for (int i = 0; i < count; ++i)
        scratch[i] = line[i * stride];
    const int width = 2 * radius + 1;
    qint64 a = 0, r = 0, g = 0, b = 0;
    auto add = [&](quint32 p, qint64 sign) {
        a += sign * static_cast<qint64>(p >> 24);
        r += sign * static_cast<qint64>((p >> 16) & 0xff);
        g += sign * static_cast<qint64>((p >> 8) & 0xff);
        b += sign * static_cast<qint64>(p & 0xff);
    };
    for (int i = 0; i < std::min(radius, count); ++i)
        add(scratch[i], 1);
    for (int i = 0; i < count; ++i) {
        const int in = i + radius;
        const int out = i - radius - 1;
        if (in < count)
            add(scratch[in], 1);
        if (out >= 0)
            add(scratch[out], -1);
        line[i * stride] = (static_cast<quint32>(a / width) << 24) | (static_cast<quint32>(r / width) << 16)
                           | (static_cast<quint32>(g / width) << 8) | static_cast<quint32>(b / width);
    }
}

} // namespace

namespace blur {

void gaussian(QImage &image, double sigma)
{
    if (sigma < 0.5 || image.isNull())
        return;
    Q_ASSERT(image.format() == QImage::Format_ARGB32_Premultiplied);
    const int w = image.width();
    const int h = image.height();
    QList<quint32> scratch(std::max(w, h));
    auto *pixels = reinterpret_cast<quint32 *>(image.bits());
    const int stride = static_cast<int>(image.bytesPerLine() / 4);
    for (const int width : boxWidths(sigma, 3)) {
        const int radius = (width - 1) / 2;
        for (int y = 0; y < h; ++y)
            boxLine(pixels + y * stride, w, 1, radius, scratch.data());
        for (int x = 0; x < w; ++x)
            boxLine(pixels + x, h, stride, radius, scratch.data());
    }
}

qreal deviceScale(const QPainter *painter, qreal logicalWidth)
{
    const QPaintDevice *device = painter->device();
    return device && logicalWidth > 0 ? device->width() / logicalWidth : 1.0;
}

void paintBlurredPath(QPainter *painter, const QRectF &area, const QPainterPath &path, const QColor &color,
                      qreal sigma, qreal scale, qreal detail)
{
    if (area.isEmpty())
        return;
    const qreal deviceSigma = sigma * scale;
    const qreal resolution = deviceSigma > detail ? std::max<qreal>(0.125, detail / deviceSigma) : 1.0;
    const qreal factor = scale * resolution;
    QImage image(QSize(static_cast<int>(std::ceil(area.width() * factor)), static_cast<int>(std::ceil(area.height() * factor))),
                 QImage::Format_ARGB32_Premultiplied);
    if (image.isNull())
        return;
    image.fill(Qt::transparent);
    {
        QPainter p(&image);
        p.setRenderHint(QPainter::Antialiasing);
        p.scale(factor, factor);
        p.translate(-area.topLeft());
        p.fillPath(path, color);
    }
    gaussian(image, deviceSigma * resolution);
    painter->save();
    painter->setRenderHint(QPainter::SmoothPixmapTransform);
    painter->drawImage(QRectF(area.topLeft(), QSizeF(image.width() / factor, image.height() / factor)), image);
    painter->restore();
}

} // namespace blur
