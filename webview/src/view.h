#pragma once

#include <QWidget>
#include <QWebEngineView>
#include <QWebEnginePage>
#include <QEventLoop>
#include <QNetworkReply>
#include <QNetworkAccessManager>
#include <QImage>

class View : public QWidget
{
    Q_OBJECT

public:
    explicit View(QWidget* parent);
    QWebEngineView* webView;  // Made public for MainWindow access

    void loadPage(const QString &uri);
    void loadImage(const QString &uri);

protected:
    void paintEvent(QPaintEvent* event) override;
    void resizeEvent(QResizeEvent* event) override;

private slots:
    void handleAuthRequest(QNetworkReply*, QAuthenticator*);

private:
    QWebEnginePage* pre_loader;
    QEventLoop pre_loader_loop;
    QNetworkAccessManager* networkManager;
    QImage currentImage;
    QImage nextImage;
};
