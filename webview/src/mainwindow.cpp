#include <QStandardPaths>
#include <QWebEngineSettings>

#include "mainwindow.h"
#include "view.h"


MainWindow::MainWindow() : QMainWindow()
{
    QWebEngineSettings::globalSettings() -> setAttribute(QWebEngineSettings::LocalStorageEnabled, true);

    // for QT5.10 and higher
    QWebEngineSettings::globalSettings() -> setAttribute(QWebEngineSettings::ShowScrollBars, false);

    view = new View(this);
    setCentralWidget(view);
}

void MainWindow::loadPage(const QString &uri)
{
    view -> loadPage(uri);
}

void MainWindow:: loadImage(const QString &uri)
{
    view -> loadImage(uri);
}
