#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#if QT_VERSION_MAJOR == 6
#include <QtWebEngineQuick>
#include <QQuickWindow>
#include <QSGRendererInterface>
#else
#include <QtWebEngine>
#endif
#include "screenlyinterface.h"

int main(int argc, char *argv[])
{
#if QT_VERSION_MAJOR == 6
    // Qt 6 specific attributes
    QQuickWindow::setGraphicsApi(QSGRendererInterface::OpenGLRhi);
#else
    // Qt 5 specific attributes
    QCoreApplication::setAttribute(Qt::AA_EnableHighDpiScaling);
    QCoreApplication::setAttribute(Qt::AA_ShareOpenGLContexts);
#endif

    QGuiApplication app(argc, argv);
#if QT_VERSION_MAJOR == 6
    QtWebEngineQuick::initialize();
#else
    QtWebEngine::initialize();
#endif

    // Create the DBus interface
    ScreenlyInterface screenlyInterface;

    QQmlApplicationEngine engine;

    // Expose the interface to QML
    engine.rootContext()->setContextProperty("screenlyInterface", &screenlyInterface);

    engine.load(QUrl(QStringLiteral("qrc:/src/main.qml")));

    if (engine.rootObjects().isEmpty()) {
        return -1;
    }

    return app.exec();
}
