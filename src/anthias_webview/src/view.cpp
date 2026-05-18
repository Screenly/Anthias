#include <QDebug>
#include <QFileInfo>
#include <QLocale>
#include <QUrl>
#include <QStandardPaths>
#include <QStringList>
#include <QWebEnginePage>
#include <QWebEngineProfile>
#include <QWebEngineSettings>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QImage>
#include <QImageReader>
#include <QPainter>
#include <QMovie>
#include <QBuffer>
#include <QByteArray>
#include <QtGlobal>

#include "view.h"

namespace {
QString getServerHost()
{
    const QByteArray value = qgetenv("LISTEN");

    if (value.isEmpty()) {
        return QStringLiteral("anthias-server");
    }

    return QString::fromUtf8(value);
}

int getServerPort()
{
    bool ok = false;
    const int value = qgetenv("PORT").toInt(&ok);

    if (!ok || value <= 0 || value > 65535) {
        return 8080;
    }

    return value;
}

// Build an Accept-Language header value from the system locale so
// multi-language URL assets serve content in the operator's configured
// language (issue #480). QLocale::system().uiLanguages() reads LANGUAGE
// (colon-separated) then LC_ALL/LANG on Linux and returns BCP47 tags
// like "nl-NL", "nl", "en-US", "en" in preference order — exactly what
// Accept-Language wants. Returns an empty string when the system is on
// the C/POSIX locale so we leave QtWebEngine's default in place rather
// than poisoning the header with "C".
QString detectAcceptLanguage()
{
    QStringList tags;
    const auto append = [&tags](const QString& tag) {
        if (tag.isEmpty()
            || tag == QLatin1String("C")
            || tag == QLatin1String("POSIX")) {
            return;
        }
        if (!tags.contains(tag, Qt::CaseInsensitive)) {
            tags.append(tag);
        }
    };

    for (const QString& lang : QLocale::system().uiLanguages()) {
        append(lang);
        // Qt 5.15 sometimes returns only the region-qualified form
        // (e.g. "nl-NL"); RFC 7231 servers will then miss a "nl"-only
        // catalog. Append the base language as a softer fallback so a
        // site that only ships generic Dutch still matches.
        const int dash = lang.indexOf(QLatin1Char('-'));
        if (dash > 0) {
            append(lang.left(dash));
        }
    }

    if (tags.isEmpty()) {
        return QString();
    }

    QString header = tags.first();
    for (int i = 1; i < tags.size(); ++i) {
        double q = 1.0 - i * 0.1;
        if (q < 0.1) {
            q = 0.1;
        }
        header += QStringLiteral(",") + tags.at(i)
                + QStringLiteral(";q=") + QString::number(q, 'f', 1);
    }
    return header;
}
}


View::View(QWidget* parent) : QWidget(parent)
{
    webView1 = new QWebEngineView(this);
    webView2 = new QWebEngineView(this);
    configureWebView(webView1);
    configureWebView(webView2);

    // Both webViews share the default profile, so the HTTP-cache setup
    // is per-process, not per-view. Use in-memory only — the default
    // on-disk cache caused URL assets to linger stale for days across
    // viewer restarts because QtWebEngine kept serving the old response
    // from /data/.cache/... (forum 983 — most-viewed bug). Memory-only
    // means the cache is dropped on every viewer restart; within a
    // single session QtWebEngine still honors the response's
    // cache-control headers. Clear once at startup to drop any disk
    // cache left behind by older builds so users upgrading from a
    // stale-cache version see fresh content on their next load.
    QWebEngineProfile* profile = QWebEngineProfile::defaultProfile();
    profile->setHttpCacheType(QWebEngineProfile::MemoryHttpCache);
    profile->clearHttpCache();

    const QString acceptLanguage = detectAcceptLanguage();
    if (!acceptLanguage.isEmpty()) {
        profile->setHttpAcceptLanguage(acceptLanguage);
        qDebug() << "Accept-Language:" << acceptLanguage;
    }

    currentWebView = webView1;
    nextWebView = webView2;
    nextWebViewReady = false;

    connect(webView1->page(), &QWebEnginePage::authenticationRequired,
            this, &View::handleAuthRequest);
    connect(webView2->page(), &QWebEnginePage::authenticationRequired,
            this, &View::handleAuthRequest);

    // QtMultimedia-backed video surface. Created hidden — only
    // made visible when ``playVideo`` fires. The QMediaPlayer +
    // QGraphicsVideoItem live for the lifetime of this widget so
    // repeated plays don't pay pipeline-rebuild cost on every
    // asset.
    videoView = new VideoView(this);
    videoView->setVisible(false);
    connect(videoView, &VideoView::videoEnded, this, &View::videoEnded);

    networkManager = new QNetworkAccessManager(this);
    movie = nullptr;
    isAnimatedImage = false;
    loadGenerationId = 0;
    reloadTimer = nullptr;
    pendingReloadIntervalS = 0;
}

View::~View()
{
    if (pageLoadConnection) {
        QObject::disconnect(pageLoadConnection);
    }
    stopReloadTimer();
    stopAnimation();
}

void View::configureWebView(QWebEngineView* view)
{
    view->settings()->setAttribute(QWebEngineSettings::LocalStorageEnabled, true);
    view->settings()->setAttribute(QWebEngineSettings::ShowScrollBars, false);
    // Match the widget's black backdrop so dark-themed URL assets don't
    // flash white between the page-load start and the first paint.
    view->page()->setBackgroundColor(Qt::black);
    view->setVisible(false);
}

void View::stopAnimation()
{
    if (movie) {
        movie->stop();
        delete movie;
        movie = nullptr;
    }
    isAnimatedImage = false;
}

void View::loadPage(const QString &uri)
{
    qDebug() << "Type: Webpage";

    const quint64 requestId = ++loadGenerationId;
    // Drop back to the web/image surface in case the previous asset
    // was a video. Stops the QMediaPlayer (frees its decoder
    // pipeline + audio device) and hides the graphics view so the
    // QWebEngineView paints are visible.
    hideVideoSurface();
    currentImage = QImage();
    stopAnimation();
    // Drop any per-asset reload timer left over from the previous
    // webpage AND the prior asset's pending interval — the viewer
    // calls setReloadInterval right after this with the new asset's
    // value, so any old pending value would be wrong if it leaked
    // into the swap that's about to happen.
    stopReloadTimer();
    pendingReloadIntervalS = 0;
    nextWebViewReady = false;

    // Drop any prior loadFinished handler before stop() — a synchronous
    // loadFinished(false) emission from the previous in-flight load
    // would otherwise reach the (still-attached) handler and run with
    // ok=false, before the new load() takes effect. With the lambda
    // detached, stop() can fire whatever it likes harmlessly.
    if (pageLoadConnection) {
        QObject::disconnect(pageLoadConnection);
    }

    nextWebView->stop();

    pageLoadConnection = connect(
        nextWebView->page(),
        &QWebEnginePage::loadFinished,
        this,
        [this, requestId](bool ok) {
            // One-shot: detach unconditionally on first fire so neither
            // a stale completion (superseded by a later load) nor a
            // re-emission (e.g., JS-driven redirect after the swap)
            // can run this lambda again.
            QObject::disconnect(pageLoadConnection);
            pageLoadConnection = QMetaObject::Connection{};

            if (requestId != loadGenerationId) {
                qDebug() << "Ignoring stale page load result";
                return;
            }

            if (ok) {
                qDebug() << "Background web page loaded successfully";
                nextWebViewReady = true;
                switchToNextWebView();
            } else {
                qDebug() << "Background web page failed to load";
                nextWebViewReady = false;
            }
        }
    );

    nextWebView->load(QUrl(uri));

    qDebug() << "Loading web page in background web view:" << uri;
}

void View::loadImage(const QString &preUri)
{
    qDebug() << "Type: Image";
    const quint64 requestId = ++loadGenerationId;

    // ``view_image('null')`` in src/anthias_viewer/__init__.py:495
    // is called AFTER ``media_player.play()`` to sweep any
    // lingering web/image background out of the way of the new
    // video — it is NOT a request to take down the freshly-
    // started video surface. Skipping ``hideVideoSurface`` for the
    // sentinel ``'null'`` URI keeps the just-started video alive;
    // calling stop() here interrupted the QMediaPlayer mid-
    // decoder init for Pi 5's Hantro G2 on 4K60 HEVC (~66 ms after
    // the first PLAYING event) and left position stuck at 0 for
    // the full 60 s asset_loop window. For a real image URI the
    // prior video must still be torn down, so the call is
    // preserved there.
    if (preUri != QLatin1String("null")) {
        hideVideoSurface();
    }

    // Cancel any pending page load so we don't keep streaming a web
    // page in the background after the user has switched to image
    // playback. Without this the QWebEngineView would continue fetching
    // and rendering until completion, even though the result would be
    // ignored by the (now stale) loadFinished handler.
    if (pageLoadConnection) {
        QObject::disconnect(pageLoadConnection);
        pageLoadConnection = QMetaObject::Connection{};
    }
    // Webpage auto-refresh only applies while a webpage is on screen;
    // killing the timer (and clearing the pending interval) here keeps
    // a stale reload from firing into the (now hidden) QWebEngineView
    // after the viewer rotates to an image.
    stopReloadTimer();
    pendingReloadIntervalS = 0;
    webView1->stop();
    webView2->stop();

    webView1->setVisible(false);
    webView2->setVisible(false);

    stopAnimation();

    QFileInfo fileInfo = QFileInfo(preUri);
    QString src;

    if (fileInfo.isFile())
    {
        qDebug() << "Location: Local File";
        qDebug() << "File path:" << fileInfo.absoluteFilePath();

        QUrl url;
        url.setScheme("http");
        url.setHost(getServerHost());
        url.setPort(getServerPort());
        url.setPath("/anthias_assets/" + fileInfo.fileName());

        src = url.toString();
        qDebug() << "Generated URL:" << src;
    }
    else if (preUri == "null")
    {
        qDebug() << "Black page";
        currentImage = QImage();
        update();
        return;
    }
    else
    {
        qDebug() << "Location: Remote URL";
        src = preUri;
    }

    qDebug() << "Loading image from:" << src;

    QNetworkRequest request(src);
    QNetworkReply* reply = networkManager->get(request);

    connect(reply, &QNetworkReply::finished, this, [this, reply, requestId]() {
        reply->deleteLater();

        if (requestId != loadGenerationId) {
            qDebug() << "Ignoring stale image response";
            return;
        }

        if (reply->error() == QNetworkReply::NoError) {
            QByteArray data = reply->readAll();
            qDebug() << "Received image data size:" << data.size();

            if (!tryLoadAsAnimatedGif(data)) {
                loadAsStaticImage(data);
            }
        } else {
            qDebug() << "Network error:" << reply->errorString();
        }
    });

    connect(reply, &QNetworkReply::errorOccurred, this,
        [this, reply, requestId](QNetworkReply::NetworkError error) {
            if (requestId != loadGenerationId) {
                return;
            }
            qDebug() << "Network error occurred:" << error;
            qDebug() << "Error string:" << reply->errorString();
        });
}

bool View::tryLoadAsAnimatedGif(const QByteArray& data)
{
    QBuffer testBuffer;
    testBuffer.setData(data);
    testBuffer.open(QIODevice::ReadOnly);

    QImageReader reader(&testBuffer);
    if (!reader.supportsAnimation() && reader.imageCount() <= 1) {
        return false;
    }

    QMovie* nextMovie = new QMovie(this);
    QBuffer* buffer = new QBuffer(nextMovie);
    buffer->setData(data);
    buffer->open(QIODevice::ReadOnly);
    nextMovie->setDevice(buffer);

    if (!nextMovie->isValid()) {
        qDebug() << "Failed to load animated image, falling back to static image";
        delete nextMovie;
        loadAsStaticImage(data);
        return true;
    }

    qDebug() << "Animated image loaded successfully. Frame count:" << nextMovie->frameCount();
    movie = nextMovie;
    setupAnimation();
    return true;
}

void View::loadAsStaticImage(const QByteArray& data)
{
    QImage newImage;
    if (newImage.loadFromData(data)) {
        qDebug() << "Successfully loaded static image. Size:" << newImage.size();
        nextImage = newImage;
        webView1->setVisible(false);
        webView2->setVisible(false);
        currentImage = nextImage;
        update();
    } else {
        qDebug() << "Failed to load image from data";
    }
}

void View::paintEvent(QPaintEvent*)
{
    QPainter painter(this);
    painter.setRenderHint(QPainter::SmoothPixmapTransform);
    painter.fillRect(rect(), Qt::black);

    if (!currentImage.isNull()) {
        QSize scaledSize = currentImage.size();
        scaledSize.scale(size(), Qt::KeepAspectRatio);
        QRect targetRect(
            (width() - scaledSize.width()) / 2,
            (height() - scaledSize.height()) / 2,
            scaledSize.width(),
            scaledSize.height()
        );
        painter.drawImage(targetRect, currentImage);
    }
}

void View::resizeEvent(QResizeEvent* event)
{
    QWidget::resizeEvent(event);
    webView1->setGeometry(rect());
    webView2->setGeometry(rect());
    if (videoView) {
        videoView->setGeometry(rect());
    }
}

void View::playVideo(const QString &uri, const QVariantMap &options)
{
    qDebug() << "Type: Video";
    ++loadGenerationId;

    // Cancel any pending QWebEngineView load so a slow page-load
    // completion doesn't race the video onto the screen mid-play.
    // Mirrors the loadImage path's handling.
    if (pageLoadConnection) {
        QObject::disconnect(pageLoadConnection);
        pageLoadConnection = QMetaObject::Connection{};
    }
    stopReloadTimer();
    pendingReloadIntervalS = 0;
    webView1->stop();
    webView2->stop();
    webView1->setVisible(false);
    webView2->setVisible(false);
    // Blank the image canvas so an old still doesn't flash through
    // before the first mpv frame paints.
    stopAnimation();
    currentImage = QImage();
    update();

    if (!videoView) {
        qWarning() << "View::playVideo: VideoView not constructed";
        return;
    }
    videoView->setGeometry(rect());
    videoView->raise();
    videoView->setVisible(true);
    videoView->play(uri, options);
}

void View::stopVideo()
{
    if (videoView) {
        videoView->stop();
    }
}

void View::hideVideoSurface()
{
    if (!videoView || !videoView->isVisible()) {
        return;
    }
    videoView->stop();
    videoView->setVisible(false);
}

void View::handleAuthRequest(const QUrl& requestUrl, QAuthenticator*)
{
    qDebug() << "Authentication required for:" << requestUrl;

    const QUrl accessDeniedUrl = QUrl::fromLocalFile(
        QStandardPaths::locate(QStandardPaths::AppDataLocation, "res/access_denied.html")
    );
    QWebEnginePage* page = qobject_cast<QWebEnginePage*>(sender());
    if (page) {
        page->load(accessDeniedUrl);
    } else {
        currentWebView->load(accessDeniedUrl);
    }
}

void View::setupAnimation()
{
    isAnimatedImage = true;
    webView1->setVisible(false);
    webView2->setVisible(false);

    connect(movie, &QMovie::frameChanged, this, [this](int) {
        if (!movie || !isAnimatedImage) {
            return;
        }

        const QImage newFrame = movie->currentImage();
        if (!newFrame.isNull()) {
            currentImage = newFrame;
            update();
        }
    });

    movie->start();
    movie->jumpToFrame(0);
    currentImage = movie->currentImage();
    update();
}

// Mirrors the v2 serializer's REFRESH_INTERVAL_S_MAX. Caps a hostile
// or buggy D-Bus caller — the multiplication ``seconds * 1000`` later
// in armReloadTimer would otherwise overflow ``int`` for values north
// of ~2.1M and produce a wraparound cadence (small or negative). The
// server side validates the range on write but the D-Bus contract is
// trust-no-one (anything on the session bus could call this).
static constexpr int kMaxReloadIntervalS = 86400;

void View::setReloadInterval(int seconds)
{
    // Per-asset auto-refresh. The viewer calls this right after each
    // loadPage() with the asset's metadata.refresh_interval_s value
    // (0 when the field is absent or explicitly disabled). Stash the
    // requested cadence and only arm the QTimer once the new page is
    // actually visible — a load is in flight when ``pageLoadConnection``
    // is set, in which case currentWebView is still the *previous*
    // page and arming now would race the swap and reload the wrong
    // page. When no load is pending — the common
    // URL-unchanged-since-last-tick case where the viewer skips
    // loadPage() — arm immediately. ``seconds`` is clamped to
    // [0, kMaxReloadIntervalS] to defend against int-overflow on the
    // millisecond multiplication done at arm time.
    if (seconds <= 0) {
        pendingReloadIntervalS = 0;
    } else if (seconds > kMaxReloadIntervalS) {
        pendingReloadIntervalS = kMaxReloadIntervalS;
    } else {
        pendingReloadIntervalS = seconds;
    }
    stopReloadTimer();

    if (!pageLoadConnection) {
        armReloadTimer();
    }
}

void View::armReloadTimer()
{
    // Idempotent: callers may invoke this multiple times around a
    // single load (setReloadInterval, then switchToNextWebView), and
    // we always want a single live timer attached to the now-visible
    // currentWebView.
    stopReloadTimer();

    if (pendingReloadIntervalS <= 0 || !currentWebView) {
        return;
    }

    reloadTimer = new QTimer(this);
    reloadTimer->setInterval(pendingReloadIntervalS * 1000);
    // Don't qDebug() the reload itself — short intervals (5–10s) would
    // flood journald to the point of unusability for very little
    // diagnostic value. A failure to load shows up via the existing
    // pageLoadConnection / loadFinished path; reload() succeeding is
    // the boring case.
    connect(reloadTimer, &QTimer::timeout, this, [this]() {
        if (currentWebView) {
            currentWebView->reload();
        }
    });
    reloadTimer->start();
}

void View::stopReloadTimer()
{
    if (reloadTimer) {
        reloadTimer->stop();
        reloadTimer->deleteLater();
        reloadTimer = nullptr;
    }
}

void View::switchToNextWebView()
{
    if (!nextWebViewReady) {
        qDebug() << "Next web view not ready yet, keeping current one visible";
        return;
    }

    qDebug() << "Switching to next web view";

    currentWebView->setVisible(false);
    nextWebView->setVisible(true);
    nextWebView->clearFocus();

    QWebEngineView* temp = currentWebView;
    currentWebView = nextWebView;
    nextWebView = temp;

    nextWebViewReady = false;

    qDebug() << "Successfully switched to next web view";

    // The new page is now visible — safe to arm the auto-refresh
    // timer against it. setReloadInterval may have been called while
    // the load was in flight; it stashed the cadence in
    // pendingReloadIntervalS and deferred to here. No-op if the asset
    // didn't request auto-refresh.
    armReloadTimer();
}
