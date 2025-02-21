#include <QStandardPaths>
#include <QWebEngineSettings>

#include "mainwindow.h"
#include "view.h"

#include <QGuiApplication>
#include <QScreen>

MainWindow::MainWindow() : QMainWindow()
{
    view = new View(this);
    view -> settings() -> setAttribute(QWebEngineSettings::LocalStorageEnabled, false);
    view -> settings() -> setAttribute(QWebEngineSettings::ShowScrollBars, false);
    setCentralWidget(view);

    QRect screenGeometry = QGuiApplication::primaryScreen()->geometry();
    setGeometry(screenGeometry);
    showFullScreen();
}

void MainWindow::loadPage(const QString &uri)
{
    view -> loadPage(uri);
}

void MainWindow:: loadImage(const QString &uri)
{
    view -> loadImage(uri);
}
