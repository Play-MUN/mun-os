#include "hairline.h"

#include <algorithm>
#include <cmath>

namespace hairline {
namespace {

// The pixels of an image and one colour blended into them.
class Canvas {
public:
    Canvas(QImage &image, QRgb colour)
        : m_bits(reinterpret_cast<QRgb *>(image.bits())), m_stride(image.bytesPerLine() / 4), m_width(image.width()),
          m_height(image.height()), m_red(qRed(colour)), m_green(qGreen(colour)), m_blue(qBlue(colour))
    {
    }

    int width() const { return m_width; }
    int height() const { return m_height; }

    // The two pixels across the line where it crosses pixel row or column
    // `major` (a column when `xMajor`), at `minor` in pixel-centre
    // coordinates across it: the nearer the centre, the more of the alpha.
    void plotPair(int major, qreal minor, qreal alpha, bool xMajor)
    {
        const qreal below = std::floor(minor);
        const int near = static_cast<int>(below);
        const qreal share = minor - below;
        const qreal weight = alpha * 256;
        const int nearWeight = static_cast<int>(weight * (1 - share) + 0.5);
        const int farWeight = static_cast<int>(weight * share + 0.5);
        if (xMajor) {
            plot(major, near, nearWeight);
            plot(major, near + 1, farWeight);
        } else {
            plot(near, major, nearWeight);
            plot(near + 1, major, farWeight);
        }
    }

private:
    // Blends the colour into pixel (x, y) by `weight`, 0 to 256.
    void plot(int x, int y, int weight)
    {
        if (weight <= 0 || static_cast<unsigned>(x) >= static_cast<unsigned>(m_width)
            || static_cast<unsigned>(y) >= static_cast<unsigned>(m_height))
            return;
        weight = std::min(weight, 256);
        QRgb &pixel = m_bits[static_cast<qsizetype>(y) * m_stride + x];
        const int keep = 256 - weight;
        pixel = qRgb((qRed(pixel) * keep + m_red * weight + 128) >> 8, (qGreen(pixel) * keep + m_green * weight + 128) >> 8,
                     (qBlue(pixel) * keep + m_blue * weight + 128) >> 8);
    }

    QRgb *m_bits;
    qsizetype m_stride;   // pixels per row
    int m_width, m_height;
    int m_red, m_green, m_blue;
};

} // namespace

void line(QImage &image, const QPointF &from, const QPointF &to, QRgb colour, qreal alphaFrom, qreal alphaTo)
{
    Canvas canvas(image, colour);
    // Pixel-centre coordinates: pixel (x, y) is centred on (x, y).
    const qreal x0 = from.x() - 0.5, y0 = from.y() - 0.5;
    const qreal dx = to.x() - from.x(), dy = to.y() - from.y();
    const bool xMajor = std::abs(dx) >= std::abs(dy);
    const qreal length = xMajor ? dx : dy;   // signed, along the major axis
    if (length == 0)
        return;

    // The part t in [t0, t1] of the line whose pixels can land in the image,
    // with a pixel of margin for the coverage's spread (Liang-Barsky).
    qreal t0 = 0, t1 = 1;
    auto clip = [&](qreal p, qreal q) {
        if (p == 0)
            return q >= 0;
        const qreal r = q / p;
        if (p < 0)
            t0 = std::max(t0, r);
        else
            t1 = std::min(t1, r);
        return true;
    };
    if (!clip(-dx, x0 + 1) || !clip(dx, canvas.width() - x0) || !clip(-dy, y0 + 1) || !clip(dy, canvas.height() - y0)
        || t0 >= t1)
        return;

    // One step per pixel row or column whose centre lies between the clipped
    // ends, the end at `to` excluded.
    const qreal major0 = xMajor ? x0 : y0, minor0 = xMajor ? y0 : x0;
    const qreal slope = (xMajor ? dy : dx) / length;
    const qreal start = major0 + length * t0, end = major0 + length * t1;
    if (length > 0) {
        for (int m = static_cast<int>(std::ceil(start)); m < end; ++m) {
            const qreal along = m - major0;
            canvas.plotPair(m, minor0 + slope * along, alphaFrom + (alphaTo - alphaFrom) * (along / length), xMajor);
        }
    } else {
        for (int m = static_cast<int>(std::floor(start)); m > end; --m) {
            const qreal along = m - major0;
            canvas.plotPair(m, minor0 + slope * along, alphaFrom + (alphaTo - alphaFrom) * (along / length), xMajor);
        }
    }
}

void circle(QImage &image, const QPointF &centre, qreal radius, QRgb colour, qreal alpha)
{
    if (radius <= 0 || alpha <= 0)
        return;
    Canvas canvas(image, colour);
    const qreal cx = centre.x() - 0.5, cy = centre.y() - 0.5;
    const qreal squared = radius * radius;
    const qreal diagonal = radius * 0.7071067811865476;
    // Top and bottom, a step per column where the circle runs flatter than
    // 45 degrees; left and right, a step per row where it runs steeper.
    const int left = std::max(0, static_cast<int>(std::floor(cx - diagonal)) + 1);
    const int right = std::min(canvas.width() - 1, static_cast<int>(std::ceil(cx + diagonal)) - 1);
    for (int x = left; x <= right; ++x) {
        const qreal d = x - cx;
        const qreal h = std::sqrt(std::max<qreal>(0, squared - d * d));
        canvas.plotPair(x, cy - h, alpha, true);
        canvas.plotPair(x, cy + h, alpha, true);
    }
    const int top = std::max(0, static_cast<int>(std::ceil(cy - diagonal)));
    const int bottom = std::min(canvas.height() - 1, static_cast<int>(std::floor(cy + diagonal)));
    for (int y = top; y <= bottom; ++y) {
        const qreal d = y - cy;
        const qreal h = std::sqrt(std::max<qreal>(0, squared - d * d));
        canvas.plotPair(y, cx - h, alpha, false);
        canvas.plotPair(y, cx + h, alpha, false);
    }
}

} // namespace hairline
