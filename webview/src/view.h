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
    void onWebPageLoadFinished(bool ok);

private:
    void configureWebView(QWebEngineView* view);
    void stopAnimation();
    bool tryLoadAsAnimatedGif(const QByteArray& data);
    void loadAsStaticImage(const QByteArray& data);
    void setupAnimation();
    void switchToNextWebView();
    void resetWebViewStates();

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
};
