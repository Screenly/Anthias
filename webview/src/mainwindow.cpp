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

    player = new QMediaPlayer;
    videoWidget = new QVideoWidget;

    player->setVideoOutput(videoWidget);
}

void MainWindow::loadPage(const QString &uri)
{
    view -> loadPage(uri);
}

void MainWindow:: loadImage(const QString &uri)
{
    view -> loadImage(uri);
}

void MainWindow::loadVideo(const QString &uri)
{
    qDebug() << "Type: Video, URI: " << uri;

    player->setMedia(QUrl(uri));
    player->play();
}
