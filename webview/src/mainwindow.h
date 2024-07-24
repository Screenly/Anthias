#pragma once

#include <QMainWindow>
#include <QWebEngineView>
#include <QMediaPlayer>
#include <QVideoWidget>

#include "view.h"

class MainWindow : public QMainWindow
{
    Q_OBJECT

    public:
        explicit MainWindow();

    public slots:
        void loadPage(const QString &uri);
        void loadImage(const QString &uri);
        void loadVideo(const QString &uri, unsigned int durationInSecs);
        bool isReady();

    private:
        View *view;
        QMediaPlayer *player;
        QVideoWidget *videoWidget;
        bool ready = true;
};
