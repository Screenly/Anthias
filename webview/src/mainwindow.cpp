#include <QStandardPaths>
#include <QTimer>
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
}

void MainWindow::loadPage(const QString &uri)
{
    view -> loadPage(uri);
}

void MainWindow:: loadImage(const QString &uri)
{
    view -> loadImage(uri);
}

void MainWindow::loadVideo(const QString &uri, unsigned int durationInSecs)
{
    qDebug() << "Type: Video, URI: " << uri << ", Duration: " << durationInSecs << "s";

    if (ready)
    {
        ready = false;
        player->setMedia(QUrl::fromLocalFile(uri));
        player->play();

        // Convert duration from seconds to milliseconds.
        unsigned int additionalDurationInMs = 500; // This prevents the video for being stopped too early.
        unsigned int durationInMs = durationInSecs * 1000 + additionalDurationInMs;

        // @TODO: Use the state() method instead to check if the video is still playing.
        // At the moment, state() returns QMediaPlayer::StoppedState even if the video is still playing.
        QTimer::singleShot(durationInMs, this, [=](){
            player->stop();
            ready = true;
        });
    }
}

bool MainWindow::isReady()
{
    return ready;
}
