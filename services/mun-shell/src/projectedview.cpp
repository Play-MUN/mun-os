#include "projectedview.h"

#include <QPainter>
#include <QQuickItemGrabResult>
#include <QQuickWindow>
#include <QSGImageNode>
#include <QSGTexture>

ProjectedView::ProjectedView(QQuickItem *parent) : QQuickItem(parent)
{
    setFlag(ItemHasContents);
}

void ProjectedView::setSource(QQuickItem *source)
{
    if (source == m_source)
        return;
    m_source = source;
    m_image = {};
    refresh();
    repaint();
    emit sourceChanged();
}

void ProjectedView::setProjection(const QMatrix4x4 &projection)
{
    if (projection == m_projection)
        return;
    m_projection = projection;
    repaint();
    emit projectionChanged();
}

void ProjectedView::refresh()
{
    if (m_pending) {
        m_again = true;
        return;
    }
    grab();
}

void ProjectedView::grab()
{
    if (!m_source || !m_source->window() || m_source->width() <= 0 || m_source->height() <= 0)
        return;
    // At the source's logical size; the image comes at the window's device
    // pixel ratio.
    m_pending = m_source->grabToImage();
    if (!m_pending)
        return;
    connect(m_pending.data(), &QQuickItemGrabResult::ready, this, [this] {
        m_image = m_pending->image();
        m_pending.reset();
        repaint();
        if (m_again) {
            m_again = false;
            grab();
        }
    });
}

void ProjectedView::repaint()
{
    m_stale = true;
    update();
}

void ProjectedView::geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry)
{
    QQuickItem::geometryChange(newGeometry, oldGeometry);
    if (newGeometry.size() != oldGeometry.size())
        repaint();
}

QSGNode *ProjectedView::updatePaintNode(QSGNode *old, UpdatePaintNodeData *)
{
    auto *node = static_cast<QSGImageNode *>(old);
    // The device pixels the projected source covers, within this item.
    const qreal ratio = window() ? window()->effectiveDevicePixelRatio() : 1;
    QRect shown;
    if (!m_image.isNull() && m_source && window()) {
        const QRectF flat(0, 0, m_source->width(), m_source->height());
        const QRectF projected = m_projection.toTransform().mapRect(flat) & boundingRect();
        shown = QRectF(projected.topLeft() * ratio, projected.size() * ratio).toAlignedRect();
    }
    if (shown.isEmpty()) {
        delete node;
        return nullptr;
    }
    if (!node) {
        node = window()->createImageNode();
        node->setOwnsTexture(true);
        m_stale = true;
    }
    if (m_stale || !node->texture() || node->texture()->textureSize() != shown.size()) {
        QImage image(shown.size(), QImage::Format_ARGB32_Premultiplied);
        image.fill(Qt::transparent);
        QPainter painter(&image);
        painter.setRenderHints(QPainter::Antialiasing | QPainter::SmoothPixmapTransform);
        painter.translate(-shown.topLeft());
        painter.scale(ratio, ratio);
        painter.setTransform(m_projection.toTransform(), true);
        painter.drawImage(QRectF(0, 0, m_source->width(), m_source->height()), m_image);
        painter.end();
        node->setTexture(window()->createTextureFromImage(image, QQuickWindow::TextureHasAlphaChannel));
        m_stale = false;
    }
    node->setRect(QRectF(QPointF(shown.topLeft()) / ratio, QSizeF(shown.size()) / ratio));
    node->setSourceRect(QRectF(QPointF(0, 0), node->texture()->textureSize()));
    return node;
}

QPointF ProjectedView::toSource(const QPointF &point) const
{
    bool invertible = false;
    const QTransform inverse = m_projection.toTransform().inverted(&invertible);
    return invertible ? inverse.map(point) : point;
}
