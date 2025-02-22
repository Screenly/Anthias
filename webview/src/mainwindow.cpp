#include <QStandardPaths>
#include <QTimer>
#include <QWebEngineSettings>

#include "mainwindow.h"
#include "view.h"

MainWindow::MainWindow() : QMainWindow()
{
    view = new View(this);
    view -> settings() -> setAttribute(QWebEngineSettings::LocalStorageEnabled, false);
    view -> settings() -> setAttribute(QWebEngineSettings::ShowScrollBars, false);
    setCentralWidget(view);

    player = new QMediaPlayer(this, QMediaPlayer::Flags(QMediaPlayer::VideoSurface));
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

void MainWindow::loadVideo(const QString &uri, unsigned int durationInSecs)
{
    qDebug() << "Type: Video, URI: " << uri << ", Duration: " << durationInSecs << "s";

    if (ready)
    {
        ready = false;
        player->setMedia(QUrl::fromLocalFile(uri));

        // Show a blank screen for a second before playing the video.
        // This is to prevent the non-video content from being displayed for a short time
        // after the video has finished playing.
        view->loadImage("null");
        QEventLoop loop;
        QTimer::singleShot(1000, &loop, SLOT(quit()));
        loop.exec();

        view->hide();
        videoWidget->setFullScreen(true);
        videoWidget->show();

        player->play();

        // Convert duration from seconds to milliseconds.
        unsigned int additionalDurationInMs = 1000; // This prevents the video for being stopped too early.
        unsigned int durationInMs = durationInSecs * 1000 + additionalDurationInMs;

        // @TODO: Use the state() method instead to check if the video is still playing.
        // At the moment, state() returns QMediaPlayer::StoppedState even if the video is still playing.
        QTimer::singleShot(durationInMs, this, [=](){
            player->stop();
            ready = true;

            videoWidget->hide();
            view->show();
        });
    }
}

bool MainWindow::isReady()
{
    return ready;
}
