#include "renderinfo.h"

#include <QCoreApplication>
#include <QJSEngine>
#include <QOpenGLContext>
#include <QOpenGLFunctions>
#include <QSGRendererInterface>
#include <QThread>

namespace {

QString apiName(QSGRendererInterface::GraphicsApi api)
{
    switch (api) {
    case QSGRendererInterface::Software: return QStringLiteral("software");
    case QSGRendererInterface::OpenGL: return QStringLiteral("opengl");
    case QSGRendererInterface::Vulkan: return QStringLiteral("vulkan");
    case QSGRendererInterface::Metal: return QStringLiteral("metal");
    case QSGRendererInterface::Direct3D11: return QStringLiteral("d3d11");
    case QSGRendererInterface::Direct3D12: return QStringLiteral("d3d12");
    case QSGRendererInterface::Null: return QStringLiteral("null");
    default: return QStringLiteral("unknown");
    }
}

// What Qt Quick would use if the scene graph started now: QT_QUICK_BACKEND
// (or setSceneGraphBackend) chooses an adaptation apart from the RHI's API.
QSGRendererInterface::GraphicsApi requestedApi()
{
    if (QQuickWindow::sceneGraphBackend() == QLatin1String("software"))
        return QSGRendererInterface::Software;
    return QQuickWindow::graphicsApi();
}

QString glString(QOpenGLFunctions *gl, GLenum name)
{
    const auto *text = reinterpret_cast<const char *>(gl->glGetString(name));
    return text ? QString::fromLatin1(text) : QString();
}

} // namespace

RenderInfo::RenderInfo() : QObject(QCoreApplication::instance())
{
    const QSGRendererInterface::GraphicsApi api = requestedApi();
    m_api = apiName(api);
    m_rhi = QSGRendererInterface::isApiRhiBased(api);
}

RenderInfo *RenderInfo::instance()
{
    static RenderInfo *one = new RenderInfo;
    return one;
}

RenderInfo *RenderInfo::create(QQmlEngine *, QJSEngine *)
{
    RenderInfo *one = instance();
    QJSEngine::setObjectOwnership(one, QJSEngine::CppOwnership);
    return one;
}

bool RenderInfo::softwareRenderer(const QString &renderer)
{
    for (const char *name : {"llvmpipe", "softpipe", "Software Rasterizer", "swrast", "SwiftShader"}) {
        if (renderer.contains(QLatin1String(name), Qt::CaseInsensitive))
            return true;
    }
    return false;
}

void RenderInfo::setWindow(QQuickWindow *window)
{
    if (window == m_window)
        return;
    if (m_window)
        disconnect(m_window, nullptr, this, nullptr);
    m_window = window;
    if (m_window) {
        // Emitted where the scene graph renders (the render thread under the
        // threaded loop) with its context current: read there, handed over
        // queued.
        connect(m_window, &QQuickWindow::sceneGraphInitialized, this, &RenderInfo::onSceneGraph,
                Qt::DirectConnection);
    }
    emit windowChanged();
}

void RenderInfo::onSceneGraph()
{
    // Possibly the render thread: only reads, then hands over.
    QQuickWindow *window = m_window;
    if (!window)
        return;
    QString api = apiName(window->rendererInterface()->graphicsApi());
    QString vendor, renderer, version;
    if (QOpenGLContext *context = QOpenGLContext::currentContext()) {
        QOpenGLFunctions *gl = context->functions();
        vendor = glString(gl, GL_VENDOR);
        renderer = glString(gl, GL_RENDERER);
        version = glString(gl, GL_VERSION);
    }
    const bool threaded = QThread::currentThread() != thread();
    if (threaded) {
        QMetaObject::invokeMethod(this, [this, api, vendor, renderer, version] {
            setDetected(api, vendor, renderer, version, true);
        }, Qt::QueuedConnection);
    } else {
        setDetected(api, vendor, renderer, version, false);
    }
}

void RenderInfo::setDetected(const QString &api, const QString &vendor, const QString &renderer,
                             const QString &version, bool threaded)
{
    if (m_known)
        return;
    m_known = true;
    const QString asked = m_api;
    m_api = api;
    m_rhi = api != QLatin1String("software");
    m_renderer = renderer;
    QString why;
    if (!m_rhi) {
        m_accelerated = false;
        why = QStringLiteral("Qt Quick's software renderer");
    } else if (api == QLatin1String("opengl") && renderer.isEmpty()) {
        m_accelerated = false;
        why = QStringLiteral("no OpenGL renderer could be read");
    } else if (softwareRenderer(renderer)) {
        m_accelerated = false;
        why = QStringLiteral("a software OpenGL renderer");
    } else {
        m_accelerated = true;
        why = QStringLiteral("a hardware renderer");
    }
    if (asked != api)
        why += QStringLiteral(" (%1 was asked for)").arg(asked);
    const QString name = renderer.isEmpty() ? QString()
        : QStringLiteral(" on %1 (%2, %3)").arg(renderer, vendor, version);
    qInfo("mun-shell: renderer: %s%s, %s render loop: %s, %s", qPrintable(api), qPrintable(name),
          threaded ? "threaded" : "basic", m_accelerated ? "accelerated" : "not accelerated", qPrintable(why));
    emit detected();
}
