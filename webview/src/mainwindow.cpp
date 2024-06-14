#include <QStandardPaths>
#include <QWebEngineSettings>
#include <QVideoWidget>
#include <QMediaPlayer>

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

void MainWindow::loadVideo(const QString &uri)
{
    qDebug() << "Type: Image, URI: " << uri;

    QMediaPlayer *player = new QMediaPlayer;
    QVideoWidget *videoWidget = new QVideoWidget;
    player -> setVideoOutput(videoWidget);
    player -> setMedia(QUrl(uri));
    player -> play();
}
