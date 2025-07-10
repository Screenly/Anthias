#pragma once

#include <QWidget>
#include <QWebEngineView>
#include <QWebEnginePage>
#include <QEventLoop>
#include <QNetworkReply>
#include <QNetworkAccessManager>
#include <QImage>
#include <QMovie>
#include <QTimer>

class View : public QWidget
{
    Q_OBJECT

public:
    explicit View(QWidget* parent);
    ~View();
    QWebEngineView* webView;  // Made public for MainWindow access

    void loadPage(const QString &uri);
    void loadImage(const QString &uri);

protected:
    void paintEvent(QPaintEvent* event) override;
    void resizeEvent(QResizeEvent* event) override;

private slots:
    void handleAuthRequest(QNetworkReply*, QAuthenticator*);
    void updateMovieFrame();
    void onWebPageLoadFinished(bool ok);
    void onWebPageLoadProgress(int progress);

private:
    bool tryLoadAsAnimatedGif(const QByteArray& data);
    void loadAsStaticImage(const QByteArray& data);
    void scheduleNextFrame();
    void setupAnimation();
    void switchToNextWebView();
    void resetWebViewStates();

    QWebEnginePage* pre_loader;
    QEventLoop pre_loader_loop;
    QNetworkAccessManager* networkManager;
    QImage currentImage;
    QImage nextImage;
    QMovie* movie;
    QTimer* animationTimer;
    bool isAnimatedImage;

    // Dual web view system
    QWebEngineView* webView1;
    QWebEngineView* webView2;
    QWebEngineView* currentWebView;
    QWebEngineView* nextWebView;
    bool nextWebViewReady;
};
