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
}

void View::loadPage(const QString &uri)
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
            QImage newImage;
            QByteArray data = reply->readAll();
            qDebug() << "Received image data size:" << data.size();

            if (newImage.loadFromData(data)) {
                qDebug() << "Successfully loaded image. Size:" << newImage.size();
                nextImage = newImage;
                // Only hide web view and update current image after the new image is loaded
                webView->setVisible(false);
                currentImage = nextImage;
                update();
            } else {
                qDebug() << "Failed to load image from data";
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

void View::paintEvent(QPaintEvent*)
{
    QPainter painter(this);
    painter.fillRect(rect(), Qt::black);

    if (!currentImage.isNull()) {
        qDebug() << "Painting image. Size:" << currentImage.size();
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
