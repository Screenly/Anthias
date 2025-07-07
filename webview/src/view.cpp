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

#include "view.h"


View::View(QWidget* parent) : QWidget(parent)
{
    webView = new QWebEngineView(this);
    webView->setVisible(false);

    connect(
        webView->page(),
        SIGNAL(authenticationRequired(QNetworkReply*,QAuthenticator*)),
        this,
        SLOT(handleAuthRequest(QNetworkReply*,QAuthenticator*))
    );

    pre_loader = new QWebEnginePage;
    networkManager = new QNetworkAccessManager(this);
    currentImage = QImage();
    nextImage = QImage();
    movie = nullptr;
    animationTimer = new QTimer(this);
    isAnimatedImage = false;

    connect(animationTimer, &QTimer::timeout, this, &View::updateMovieFrame);
}

View::~View()
{
    if (movie) {
        movie->stop();
        delete movie;
    }
}

void View::loadPage(const QString &uri, qreal zoomFactor)
{
    qDebug() << "Type: Webpage";

    // Clear current image if any
    currentImage = QImage();

    // Connect to loadFinished signal with version-specific code
    connect(webView->page(), &QWebEnginePage::loadFinished, this, [=](bool ok) {
        if (ok) {
            qDebug() << "Web page loaded successfully";
            webView->setVisible(true);
            webView->clearFocus();
            webView->setZoomFactor(zoomFactor);
        } else {
            qDebug() << "Web page failed to load";
        }
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    }, Qt::SingleShotConnection);  // Disconnect after first signal
#else
    });
#endif

    // Load the page
    webView->stop();
    webView->load(QUrl(uri));
}

void View::loadImage(const QString &preUri)
{
    qDebug() << "Type: Image";

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
    QNetworkReply* reply = networkManager->get(request);

    connect(reply, &QNetworkReply::finished, this, [=]() {
        if (reply->error() == QNetworkReply::NoError) {
            QByteArray data = reply->readAll();
            qDebug() << "Received image data size:" << data.size();

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
                } else {
                    qDebug() << "Failed to load GIF as animation, falling back to static image";
                    delete movie;
                    movie = nullptr;
                    delete buffer;

                    // Fall back to static image loading
                    QImage newImage;
                    if (newImage.loadFromData(data)) {
                        qDebug() << "Successfully loaded image as static. Size:" << newImage.size();
                        nextImage = newImage;
                        webView->setVisible(false);
                        currentImage = nextImage;
                        update();
                    } else {
                        qDebug() << "Failed to load image from data";
                    }
                }
            } else {
                // This is a static image (including single-frame GIFs)
                delete testBuffer;

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
    webView->setGeometry(rect());
}

void View::handleAuthRequest(QNetworkReply* reply, QAuthenticator* auth)
{
    Q_UNUSED(reply)
    Q_UNUSED(auth)
    webView->load(QUrl::fromLocalFile(QStandardPaths::locate(QStandardPaths::AppDataLocation, "res/access_denied.html")));
}
