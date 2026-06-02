#include <QGuiApplication>
#include <QScreen>

#include "mainwindow.h"
#include "view.h"


MainWindow::MainWindow() : QMainWindow()
{
    view = new View(this);
    setCentralWidget(view);
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    // Re-emit VideoView's EOF up to MainWindow so D-Bus
    // ExportAllSignals exposes a single ``videoEnded`` signal on
    // ``/Anthias`` (the same object path Python subscribes to for
    // the existing slots).
    connect(view, &View::videoEnded, this, &MainWindow::videoEnded);
#endif
    // NOTE: the window is shown (showFullScreen) by main() after
    // construction, not here — showing in both places double-committed
    // the surface and tripped a wlroots xdg_surface warning under cage.
}

void MainWindow::loadPage(const QString &uri)
{
    view->loadPage(uri);
}

void MainWindow::loadImage(const QString &uri)
{
    view->loadImage(uri);
}

void MainWindow::setReloadInterval(int seconds)
{
    view->setReloadInterval(seconds);
}

#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
void MainWindow::playVideo(const QString &uri, const QVariantMap &options)
{
    view->playVideo(uri, options);
}

void MainWindow::stopVideo()
{
    view->stopVideo();
}
#endif
