#include <QDebug>
#include <QFileInfo>
#include <QUrl>
#include <QStandardPaths>
#include <QEventLoop>
#include <QTimer>

#include "view.h"


View::View(QWidget* parent) : QWebEngineView(parent)
{
    connect(
        QWebEngineView::page(),
        SIGNAL(authenticationRequired(QNetworkReply*,QAuthenticator*)),
        this,
        SLOT(handleAuthRequest(QNetworkReply*,QAuthenticator*))
    );
    pre_loader = new QWebEnginePage;
}

void View::loadPage(const QString &uri)
{
    qDebug() << "Type: Webpage";
    stop();
    load(QUrl(uri));
    clearFocus();
}

void View::loadImage(const QString &preUri)
{
    qDebug() << "Type: Image";
    QFileInfo fileInfo = QFileInfo(preUri);
    QString src;

    if (fileInfo.isFile())
    {
        qDebug() << "Location: Local File";

        QUrl url;
        url.setScheme("http");
        url.setHost("anthias-nginx");
        url.setPath("/screenly_assets/" + fileInfo.fileName());

        src = url.toString();
    }
    else if (preUri == "null")
    {
        qDebug() << "Black page";
    }
    else
    {
        qDebug() << "Location: Remote URL";
        src = preUri;
    }

    qDebug() << "Current src: " + src;

    QString script = "window.setimg=function(n){var o=new Image;o.onload=function()"
                     "{document.body.style.backgroundSize=o.width>window.innerWidth||o.height>window.innerHeight?\"contain\":\"auto\","
                     "document.body.style.backgroundImage=\"url('\"+n+\"')\","
                     "document.body.style.opacity=\"1\"},"
                     "o.onerror=function(){document.body.style.opacity=\"1\"},"
                     "document.body.style.opacity=\"0.5\","
                     "o.src=n};";
    QString styles = "background: #000 center no-repeat; transition: opacity 0.3s ease-in-out";

    // Don't stop the current view, let it stay visible
    pre_loader->setHtml("<html><head><script>" + script + "</script></head><body style='" + styles + "'><script>window.setimg(\"" + src + "\");</script></body></html>");

    connect(pre_loader, &QWebEnginePage::loadFinished, this, [=](bool result){
        if (result)
        {
            pre_loader->toHtml([&](const QString &result){
                setHtml(result);
            });
        }
    });
}

void View::handleAuthRequest(QNetworkReply* reply, QAuthenticator* auth)
{
    Q_UNUSED(reply)
    Q_UNUSED(auth)
    load(QUrl::fromLocalFile(QStandardPaths::locate(QStandardPaths::AppDataLocation, "res/access_denied.html")));
}
