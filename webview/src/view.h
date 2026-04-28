#pragma once

#include <QWidget>
#include <QWebEngineView>
#include <QAuthenticator>
#include <QNetworkAccessManager>
#include <QImage>
#include <QMovie>
#include <QUrl>

class View : public QWidget
{
    Q_OBJECT

public:
    explicit View(QWidget* parent);
    ~View();

    void loadPage(const QString &uri);
    void loadImage(const QString &uri);

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

    QNetworkAccessManager* networkManager;
    QImage currentImage;
    QImage nextImage;
    QMovie* movie;
    bool isAnimatedImage;
    quint64 loadGenerationId;

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
};
