#pragma once

#include <QMainWindow>
#include <QVariantMap>
#include <QWebEngineView>

#include "view.h"

class MainWindow : public QMainWindow
{
    Q_OBJECT

    public:
        explicit MainWindow();

    public slots:
        void loadPage(const QString &uri);
        void loadImage(const QString &uri);
        void setReloadInterval(int seconds);
        // libmpv-in-Qt video playback (issue #2904). Replaces the
        // external mpv subprocess MPVMediaPlayer used to launch from
        // src/anthias_viewer/media_player.py. ``options`` mirrors the
        // mpv option set the subprocess path used to assemble as
        // argv: ``hwdec``, ``audio-device``, ``video-sync``,
        // ``vd-lavc-threads``, ``video-rotate``. Values are coerced
        // to UTF-8 strings via QVariant::toString().
        void playVideo(const QString &uri, const QVariantMap &options);
        void stopVideo();

    signals:
        // Re-emitted from VideoView::videoEnded — exported over
        // D-Bus by main.cpp's QDBusConnection::ExportAllSignals so
        // Python can subscribe in a future revision (the asset_loop
        // currently just sleeps for ``duration``).
        void videoEnded();

    private:
        View *view = nullptr;
};
