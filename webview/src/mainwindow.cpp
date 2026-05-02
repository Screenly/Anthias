#include <QGuiApplication>
#include <QScreen>

#include "mainwindow.h"
#include "view.h"


MainWindow::MainWindow() : QMainWindow()
{
    view = new View(this);
    setCentralWidget(view);

    showFullScreen();
}

void MainWindow::loadPage(const QString &uri)
{
    view->loadPage(uri);
}

void MainWindow::loadImage(const QString &uri)
{
    view->loadImage(uri);
}
