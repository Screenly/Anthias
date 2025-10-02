#include <QDebug>
#include <QFileInfo>
#include <QUrl>
#include <QStandardPaths>
#include <QEventLoop>
#include <QTimer>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QImage>
#include <QPainter>
#include <QMovie>
#include <QBuffer>
#include <QWebEngineProfile>

#include "view.h"
#include "requestinterceptor.h"


View::View(QWidget* parent) : QWidget(parent)
{
    // Initialize dual web view system
    webView1 = new QWebEngineView(this);
    webView2 = new QWebEngineView(this);

    // Set up initial state
    currentWebView = webView1;
    nextWebView = webView2;
    nextWebViewReady = false;

    // Make webView1 the main webView for compatibility
    webView = webView1;
    webView->setVisible(false);

    // Connect authentication for both web views
    connect(webView1->page(), SIGNAL(authenticationRequired(QNetworkReply*,QAuthenticator*)),
            this, SLOT(handleAuthRequest(QNetworkReply*,QAuthenticator*)));
    connect(webView2->page(), SIGNAL(authenticationRequired(QNetworkReply*,QAuthenticator*)),
            this, SLOT(handleAuthRequest(QNetworkReply*,QAuthenticator*)));

    pre_loader = new QWebEnginePage;
    networkManager = new QNetworkAccessManager(this);
    currentImage = QImage();
    nextImage = QImage();
    movie = nullptr;
    animationTimer = new QTimer(this);
    isAnimatedImage = false;

    connect(animationTimer, &QTimer::timeout, this, &View::updateMovieFrame);

#if QT_VERSION >= QT_VERSION_CHECK(5, 6, 0)
    // Attach request interceptor to inject headers
    RequestInterceptor* interceptor = new RequestInterceptor(this);
    webView1->page()->profile()->setUrlRequestInterceptor(interceptor);
    webView2->page()->profile()->setUrlRequestInterceptor(interceptor);
#endif
}

View::~View()
{
    if (movie) {
        movie->stop();
        delete movie;
    }
}

void View::loadPage(const QString &uri)
{
    qDebug() << "Type: Webpage";

    // Clear current image if any
    currentImage = QImage();

    // Stop any existing animation
    if (movie) {
        movie->stop();
        delete movie;
        movie = nullptr;
    }
    animationTimer->stop();
    isAnimatedImage = false;

    // Reset web view states
    resetWebViewStates();

    // Connect to load progress and finished signals for the next web view
    connect(nextWebView->page(), &QWebEnginePage::loadProgress, this, &View::onWebPageLoadProgress);
    connect(nextWebView->page(), &QWebEnginePage::loadFinished, this, &View::onWebPageLoadFinished);

    // Load the page in the next web view while keeping current one visible
    nextWebView->stop();
    nextWebView->load(QUrl(uri));

    qDebug() << "Loading web page in background web view:" << uri;
}

void View::loadImage(const QString &preUri)
{
    qDebug() << "Type: Image";

    // Hide both web views when switching to image
    webView1->setVisible(false);
    webView2->setVisible(false);

    // Stop any existing animation
    if (movie) {
        movie->stop();
        delete movie;
        movie = nullptr;
    }
    animationTimer->stop();
    isAnimatedImage = false;

    QFileInfo fileInfo = QFileInfo(preUri);
    QString src;

    if (fileInfo.isFile())
    {
        qDebug() << "Location: Local File";
        qDebug() << "File path:" << fileInfo.absoluteFilePath();

        QUrl url;
        url.setScheme("http");
        url.setHost("anthias-nginx");
        url.setPath("/screenly_assets/" + fileInfo.fileName());

        src = url.toString();
        qDebug() << "Generated URL:" << src;
    }
    else if (preUri == "null")
    {
        qDebug() << "Black page";
        currentImage = QImage();
        webView->setVisible(false);
        update();
        return;
    }
    else
    {
        qDebug() << "Location: Remote URL";
        src = preUri;
    }

    qDebug() << "Loading image from:" << src;

    // Start loading the next image
    QNetworkRequest request(src);
    // Inject the same headers for image fetches
    QByteArray hostname = qgetenv("ANTHIAS_HOSTNAME");
    QByteArray version = qgetenv("ANTHIAS_VERSION");
    QByteArray mac = qgetenv("ANTHIAS_MAC");
    if (!hostname.isEmpty()) {
        request.setRawHeader("X-Anthias-hostname", hostname);
    }
    if (!version.isEmpty()) {
        request.setRawHeader("X-Anthias-version", version);
    }
    if (!mac.isEmpty()) {
        request.setRawHeader("X-Anthias-mac", mac);
    }
    QNetworkReply* reply = networkManager->get(request);

    connect(reply, &QNetworkReply::finished, this, [=]() {
        if (reply->error() == QNetworkReply::NoError) {
            QByteArray data = reply->readAll();
            qDebug() << "Received image data size:" << data.size();

            if (tryLoadAsAnimatedGif(data)) {
                // Successfully loaded as animated GIF
                return;
            } else {
                // Load as static image
                loadAsStaticImage(data);
            }
        } else {
            qDebug() << "Network error:" << reply->errorString();
        }
        reply->deleteLater();
    });

    connect(reply, &QNetworkReply::errorOccurred, this, [=](QNetworkReply::NetworkError error) {
        qDebug() << "Network error occurred:" << error;
        qDebug() << "Error string:" << reply->errorString();
    });
}

bool View::tryLoadAsAnimatedGif(const QByteArray& data)
{
    // Try to load as QMovie first to check if it's an animated GIF
    QBuffer* testBuffer = new QBuffer();
    testBuffer->setData(data);
    testBuffer->open(QIODevice::ReadOnly);

    // Create QMovie to test if it's animated
    QMovie testMovie(testBuffer, QByteArray(), this);

    if (testMovie.isValid() && testMovie.frameCount() > 1) {
        // This is an animated GIF
        qDebug() << "Detected animated GIF with" << testMovie.frameCount() << "frames";
        delete testBuffer; // Clean up test buffer

        // Create a new buffer for the actual movie
        QBuffer* buffer = new QBuffer();
        buffer->setData(data);
        buffer->open(QIODevice::ReadOnly);

        // Create the actual movie for animation
        movie = new QMovie(buffer, QByteArray(), this);

        if (movie->isValid()) {
            qDebug() << "GIF animation loaded successfully. Frame count:" << movie->frameCount();
            qDebug() << "Animation speed:" << movie->speed() << "ms per frame";

            setupAnimation();
            return true;
        } else {
            qDebug() << "Failed to load GIF as animation, falling back to static image";
            delete movie;
            movie = nullptr;
            delete buffer;

            // Fall back to static image loading - this preserves original behavior
            loadAsStaticImage(data);
            return true; // Return true to prevent double loading
        }
    } else {
        // This is a static image (including single-frame GIFs)
        delete testBuffer;
        return false;
    }
}

void View::loadAsStaticImage(const QByteArray& data)
{
    QImage newImage;
    if (newImage.loadFromData(data)) {
        qDebug() << "Successfully loaded static image. Size:" << newImage.size();
        nextImage = newImage;
        webView->setVisible(false);
        currentImage = nextImage;
        update();
    } else {
        qDebug() << "Failed to load image from data";
    }
}

void View::updateMovieFrame()
{
    if (movie && isAnimatedImage && movie->state() == QMovie::Running) {
        // Try to advance to the next frame
        if (movie->jumpToNextFrame()) {
            QImage newFrame = movie->currentImage();
            if (!newFrame.isNull()) {
                currentImage = newFrame;
                update();
            }
        }

        // Schedule next frame update
        scheduleNextFrame();
    }
}

void View::scheduleNextFrame()
{
    int frameDelay = movie->nextFrameDelay();
    if (frameDelay > 0) {
        animationTimer->start(frameDelay);
    } else {
        // If no delay specified, try to get it from the movie speed
        frameDelay = movie->speed();
        if (frameDelay > 0) {
            animationTimer->start(frameDelay);
        } else {
            animationTimer->start(100); // Default delay
        }
    }
}

void View::paintEvent(QPaintEvent*)
{
    QPainter painter(this);
    painter.fillRect(rect(), Qt::black);

    if (!currentImage.isNull()) {
        // Only log for static images to avoid spam during animation
        if (!isAnimatedImage) {
            qDebug() << "Painting image. Size:" << currentImage.size();
        }
        QSize scaledSize = currentImage.size();
        scaledSize.scale(size(), Qt::KeepAspectRatio);
        QRect targetRect = QRect(QPoint(0, 0), size());
        targetRect = QRect(
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
    // Both web views should have the same geometry
    webView1->setGeometry(rect());
    webView2->setGeometry(rect());
}

void View::handleAuthRequest(QNetworkReply* reply, QAuthenticator* auth)
{
    Q_UNUSED(reply)
    Q_UNUSED(auth)
    webView->load(QUrl::fromLocalFile(QStandardPaths::locate(QStandardPaths::AppDataLocation, "res/access_denied.html")));
}

void View::setupAnimation()
{
    isAnimatedImage = true;
    webView->setVisible(false);

    // Start the animation
    movie->start();

    // Set up timer for frame updates
    int frameDelay = movie->nextFrameDelay();
    if (frameDelay > 0) {
        animationTimer->start(frameDelay);
    } else {
        // Default to 100ms if no delay specified
        animationTimer->start(100);
    }

    // Get the first frame
    currentImage = movie->currentImage();
    update();
}

void View::onWebPageLoadFinished(bool ok)
{
    if (ok) {
        qDebug() << "Background web page loaded successfully";
        nextWebViewReady = true;

        // Switch to the new web view since it's ready
        switchToNextWebView();
    } else {
        qDebug() << "Background web page failed to load";
        nextWebViewReady = false;
    }

    // Disconnect signals to prevent memory leaks
    disconnect(nextWebView->page(), &QWebEnginePage::loadProgress, this, &View::onWebPageLoadProgress);
    disconnect(nextWebView->page(), &QWebEnginePage::loadFinished, this, &View::onWebPageLoadFinished);
}

void View::onWebPageLoadProgress(int progress)
{
    qDebug() << "Background web page load progress:" << progress << "%";

    // If progress reaches 100%, mark as ready
    if (progress >= 100) {
        nextWebViewReady = true;
    }
}

void View::switchToNextWebView()
{
    if (!nextWebViewReady) {
        qDebug() << "Next web view not ready yet, keeping current one visible";
        return;
    }

    qDebug() << "Switching to next web view";

    // Hide current web view
    currentWebView->setVisible(false);

    // Show next web view
    nextWebView->setVisible(true);
    nextWebView->clearFocus();

    // Swap the web views
    QWebEngineView* temp = currentWebView;
    currentWebView = nextWebView;
    nextWebView = temp;

    // Update the main webView reference for compatibility
    webView = currentWebView;

    // Reset states for next load
    nextWebViewReady = false;

    qDebug() << "Successfully switched to next web view";
}

void View::resetWebViewStates()
{
    nextWebViewReady = false;

    // Disconnect any existing signals to prevent duplicates
    disconnect(nextWebView->page(), &QWebEnginePage::loadProgress, this, &View::onWebPageLoadProgress);
    disconnect(nextWebView->page(), &QWebEnginePage::loadFinished, this, &View::onWebPageLoadFinished);
}
