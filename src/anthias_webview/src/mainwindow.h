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
        // ``skipSslVerify`` carries the asset's effective SSL policy
        // (device-wide verify_ssl composed with per-asset
        // skip_ssl_verify) computed by the Python viewer. No default
        // argument: for a slot, moc emits a cloned meta-method for each
        // trailing default (that clone is why QMetaObject::invokeMethod
        // can omit the argument), and QtDBus would export each clone as
        // its own same-named D-Bus method — two ``loadPage`` entries with
        // signatures ``s`` and ``sb``. Keeping the parameter mandatory
        // exports a single, unambiguous method the Python side always
        // calls with the flag.
        void loadPage(const QString &uri, bool skipSslVerify);
        void loadImage(const QString &uri, bool skipSslVerify);
        void setReloadInterval(int seconds);
        // Per-asset custom HTTP request headers (#2215). ``headersJson``
        // is a JSON object of ``{name: value}`` pairs. Called by the
        // viewer right before loadPage; forwarded to View which scopes
        // them to the loaded URL's origin (scheme+host+port). Un-gated
        // (Qt5 + Qt6).
        void setRequestHeaders(const QString &headersJson);
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
        // libmpv-in-Qt video playback (issue #2904). Replaces the
        // external mpv subprocess MPVMediaPlayer used to launch from
        // src/anthias_viewer/media_player.py. ``options`` mirrors the
        // mpv option set the subprocess path used to assemble as
        // argv: ``hwdec``, ``audio-device``, ``video-sync``,
        // ``vd-lavc-threads``, ``video-rotate``. Values are coerced
        // to UTF-8 strings via QVariant::toString(). Qt 5 boards
        // (Pi 1 / Pi 2 / Pi 3) route video through GstFbdevMediaPlayer on the
        // Python side and never call these slots, so they're
        // compiled out below the Qt-version gate.
        void playVideo(const QString &uri, const QVariantMap &options);
        void stopVideo();

    signals:
        // Re-emitted from VideoView::videoEnded — exported over
        // D-Bus by main.cpp's QDBusConnection::ExportAllSignals so
        // Python can subscribe in a future revision (the asset_loop
        // currently just sleeps for ``duration``).
        void videoEnded();
#endif

    private:
        View *view = nullptr;
};
