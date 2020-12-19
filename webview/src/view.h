#pragma once

#include <QWebEngineView>
#include <QWebEnginePage>
#include <QWidget>
#include <QEventLoop>

class View : public QWebEngineView
{
    Q_OBJECT

public:
    explicit View(QWidget* parent);

    void loadPage(const QString &uri);
    void loadImage(const QString &uri);

private slots:
    void handleAuthRequest(QNetworkReply*, QAuthenticator*);

private:
    QWebEnginePage* pre_loader;
    QEventLoop pre_loader_loop;
};
