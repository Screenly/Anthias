#include <QStandardPaths>
#include <QWebEngineSettings>

#include "mainwindow.h"
#include "view.h"


MainWindow::MainWindow() : QMainWindow()
{
    view = new View(this);
    view->webView->settings()->setAttribute(QWebEngineSettings::LocalStorageEnabled, true);
    view->webView->settings()->setAttribute(QWebEngineSettings::ShowScrollBars, false);
    setCentralWidget(view);
}

void MainWindow::loadPage(const QString &uri)
{
    view->loadPage(uri);
}

void MainWindow::loadImage(const QString &uri)
{
    view->loadImage(uri);
}
