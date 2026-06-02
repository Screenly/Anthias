#pragma once

#include <QWidget>
#include <QWebEngineView>
#include <QAuthenticator>
#include <QNetworkAccessManager>
#include <QImage>
#include <QMovie>
#include <QTimer>
#include <QUrl>
#include <QVariantMap>

#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
#include "videoview.h"
#endif

class View : public QWidget
{
    Q_OBJECT

public:
    explicit View(QWidget* parent);
    ~View();

    void loadPage(const QString &uri);
    void loadImage(const QString &uri);
    void setReloadInterval(int seconds);
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    // Hands the URI + option dict to VideoView (QtMultimedia
    // QMediaPlayer rendering into a QML VideoOutput) and switches
    // visibility so the video surface is on top of the
    // QWebEngineView pair / image canvas. Pauses background URL
    // loads so a parked QWebEngineView doesn't keep streaming while
    // video plays.
    //
    // Qt 5 boards (Pi 1 / Pi 2 / Pi 3) route video through GstFbdevMediaPlayer
    // painting straight to the framebuffer (see
    // ``MediaPlayerProxy.get_instance`` in
    // ``src/anthias_viewer/media_player.py``), so the in-process
    // playback surface and its EOF signal are Qt6-only.
    void playVideo(const QString &uri, const QVariantMap &options);
    void stopVideo();

signals:
    void videoEnded();
#endif

protected:
    void paintEvent(QPaintEvent* event) override;
    void resizeEvent(QResizeEvent* event) override;

private slots:
    void handleAuthRequest(const QUrl& requestUrl, QAuthenticator* authenticator);

private:
    void configureWebView(QWebEngineView* view);
    void stopAnimation();
    bool tryLoadAsAnimatedGif(const QByteArray& data);
    void loadAsStaticImage(const QByteArray& data);
    void setupAnimation();
    void switchToNextWebView();
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    // Hides VideoView and re-enables the web/image surface. Called
    // by loadPage / loadImage so a switch from video back to a web
    // page or image doesn't leave the GL widget on top of the
    // QWebEngineView.
    void hideVideoSurface();
#endif

    QNetworkAccessManager* networkManager;
    QImage currentImage;
    QImage nextImage;
    QMovie* movie;
    bool isAnimatedImage;
    quint64 loadGenerationId;

#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    // QtMultimedia-backed video widget (issue #2904). Sibling of
    // the web / image widgets — visibility is toggled rather than
    // re-parented so the QMediaPlayer + Quick scene survive
    // across plays (no pipeline rebuild per asset).
    VideoView* videoView;
#endif

    // Dual web view system
    QWebEngineView* webView1;
    QWebEngineView* webView2;
    QWebEngineView* currentWebView;
    QWebEngineView* nextWebView;
    bool nextWebViewReady;

    // Connection handle for the currently-pending loadFinished slot, so
    // we can drop it before issuing stop() on the next loadPage and
    // avoid a synchronous loadFinished(false) racing into a stale slot.
    QMetaObject::Connection pageLoadConnection;

    // Per-asset auto-refresh timer. When non-null and active, fires
    // currentWebView->reload() every ``pendingReloadIntervalS`` seconds.
    // Cleared on every loadPage / loadImage so a fresh asset starts
    // from a clean slate. Owned by the View (parent=this).
    QTimer* reloadTimer;

    // Most recently requested auto-refresh cadence, in seconds. 0 = no
    // auto-refresh. Held separately from the timer because
    // setReloadInterval can land *while a page load is still in flight*
    // (loadPage queues a load into nextWebView, then the viewer calls
    // setReloadInterval before the swap completes); arming a QTimer
    // immediately would target the still-visible *previous* page via
    // currentWebView->reload(). We instead remember the value here and
    // arm the timer in switchToNextWebView() once the new page is
    // actually visible.
    int pendingReloadIntervalS;
    void stopReloadTimer();
    void armReloadTimer();
};
