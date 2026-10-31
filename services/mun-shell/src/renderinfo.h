// RenderInfo says how the shell's window is drawn, for the parts whose drawing
// depends on it and for the journal.
//
// Two facts:
// - The graphics API Qt Quick draws with: its software renderer, as in the
//   laboratory's virtual machines (QT_QUICK_BACKEND=software), or a graphics
//   API through Qt's RHI (OpenGL on the boards). It chooses the drawing path:
//   `rhi` is true when the scene graph composes on a graphics API, so that
//   transforms, opacity and textures cost the GPU, not the CPU. Before the
//   window exists it is what was asked for (the backend and API Qt would
//   use); once the scene graph has started it is what it actually uses (a
//   graphics API that cannot start makes Qt fall back), and `rhi` changes
//   then if they differ.
// - Whether that API is accelerated: an OpenGL renderer can itself be
//   software (Mesa's llvmpipe or softpipe). Known only once the scene graph
//   has started, from its own context on its render thread. It chooses how
//   much motion is worth drawing (`accelerated`); until it is known it is
//   false, the software renderer's economy.
//
// The decision and its reason, the renderer's name and the render loop
// (basic, on the GUI thread, or threaded) go to the journal once:
//   "mun-shell: renderer: ...".
//
// A GUI-thread object; the render thread only reads its context and hands the
// readings over (queued).
#pragma once

#include <QObject>
#include <QPointer>
#include <QQuickWindow>
#include <QString>
#include <QtQml/qqmlregistration.h>

class QJSEngine;
class QQmlEngine;

class RenderInfo : public QObject {
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON
    // The window whose scene graph is described; set once by Main.qml.
    Q_PROPERTY(QQuickWindow *window READ window WRITE setWindow NOTIFY windowChanged)
    Q_PROPERTY(bool rhi READ rhi NOTIFY detected)
    Q_PROPERTY(bool accelerated READ accelerated NOTIFY detected)
    // "software", "opengl", "vulkan", ...
    Q_PROPERTY(QString api READ api NOTIFY detected)
    // The renderer as its driver names it ("FD643"); empty until known or for
    // the software renderer.
    Q_PROPERTY(QString renderer READ renderer NOTIFY detected)

public:
    // The one instance, owned by the QCoreApplication (which must exist).
    static RenderInfo *instance();
    static RenderInfo *create(QQmlEngine *, QJSEngine *);

    QQuickWindow *window() const { return m_window; }
    void setWindow(QQuickWindow *window);
    bool rhi() const { return m_rhi; }
    bool accelerated() const { return m_accelerated; }
    QString api() const { return m_api; }
    QString renderer() const { return m_renderer; }

    // Whether an OpenGL renderer string names a software rasterizer.
    static bool softwareRenderer(const QString &renderer);

signals:
    void windowChanged();
    // The scene graph started: `accelerated` and `renderer` are now known.
    void detected();

private:
    RenderInfo();
    void onSceneGraph();   // render thread
    void setDetected(const QString &api, const QString &vendor, const QString &renderer, const QString &version,
                     bool threaded);

    QPointer<QQuickWindow> m_window;
    bool m_rhi = false;
    bool m_accelerated = false;
    bool m_known = false;
    QString m_api;
    QString m_renderer;
};
