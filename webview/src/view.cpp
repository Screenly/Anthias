#include <QDebug>
#include <QFileInfo>
#include <QUrl>
#include <QStandardPaths>
#include <QWebFrame>
#include <QEventLoop>
#include <QTimer>

#include "view.h"


View::View(QWidget* parent) : QWebView(parent)
{
    // Need to convert this to a new syntax
    connect(QWebView::page()->networkAccessManager(), SIGNAL(authenticationRequired(QNetworkReply*,QAuthenticator*)),
            this, SLOT(handleAuthRequest(QNetworkReply*,QAuthenticator*)));
    pre_loader = new QWebPage;
}

void View::loadPage(const QString &uri)
{
    qDebug() << "Type: Webpage";
    stop();
    load(QUrl(uri));
}

void View::loadImage(const QString &preUri)
{
    qDebug() << "Type: Image";
    QFileInfo fileInfo = QFileInfo(preUri);
    QString uri;

    if (fileInfo.isFile())
    {
        qDebug() << "Location: Local File";
        uri = QUrl::fromLocalFile(fileInfo.absoluteFilePath()).toEncoded();
    }
    else if (preUri == "null")
    {
        qDebug() << "Black page";
    }
    else
    {
        qDebug() << "Location: Remote URL";
        uri = preUri;
    }

    QString script = "window.setimg=function(n){var o=new Image;o.onload=function()"
                     "{document.body.style.backgroundSize=o.width>window.innerWidth||o.height>window.innerHeight?\"contain\":\"auto\",document.body.style.backgroundImage=\"url(\"+n+\")\"},o.src=n};";
    QString styles = "background: #000 center no-repeat";

    stop();
    pre_loader->currentFrame()->setHtml("<html><head><script>" + script + "</script></head><body style='" + styles + "'><script>window.setimg(\"" + uri + "\");</script></body></html>");
    connect(pre_loader,SIGNAL(loadFinished(bool)),&pre_loader_loop,SLOT(quit()));
    QTimer::singleShot(5000, &pre_loader_loop, SLOT(quit()));
    pre_loader_loop.exec();
    setHtml(pre_loader->currentFrame()->toHtml());
}

void View::handleAuthRequest(QNetworkReply* reply, QAuthenticator* auth)
{
    Q_UNUSED(reply)
    Q_UNUSED(auth)
    load(QUrl::fromLocalFile(QStandardPaths::locate(QStandardPaths::DataLocation, "res/access_denied.html")));
}
