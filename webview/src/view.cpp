#include <QDebug>
#include <QFileInfo>
#include <QUrl>
#include <QStandardPaths>
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

    currentWebView = webView1;
    nextWebView = webView2;
    nextWebViewReady = false;

    connect(webView1->page(), &QWebEnginePage::authenticationRequired,
            this, &View::handleAuthRequest);
    connect(webView2->page(), &QWebEnginePage::authenticationRequired,
            this, &View::handleAuthRequest);

    networkManager = new QNetworkAccessManager(this);
    movie = nullptr;
    isAnimatedImage = false;
    loadGenerationId = 0;
}

View::~View()
{
    if (pageLoadConnection) {
        QObject::disconnect(pageLoadConnection);
    }
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
    currentImage = QImage();
    stopAnimation();
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

    // Cancel any pending page load so we don't keep streaming a web
    // page in the background after the user has switched to image
    // playback. Without this the QWebEngineView would continue fetching
    // and rendering until completion, even though the result would be
    // ignored by the (now stale) loadFinished handler.
    if (pageLoadConnection) {
        QObject::disconnect(pageLoadConnection);
        pageLoadConnection = QMetaObject::Connection{};
    }
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
}
