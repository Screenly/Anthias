#include <QApplication>
#include <QDebug>
#include <QtDBus>
#include <QtWebEngine>
#include <QWebEngineView>

#include "mainwindow.h"

int main(int argc, char *argv[])
{
    // @TODO: This is a temporary solution until we find something better
    char ARG_DISABLE_WEB_SECURITY[] = "--disable-web-security";
    int newArgc = argc+1+1;
    char** newArgv = new char*[newArgc];
    for(int i=0; i<argc; i++) {
        newArgv[i] = argv[i];
    }
    newArgv[argc] = ARG_DISABLE_WEB_SECURITY;
    newArgv[argc+1] = nullptr;

    QApplication app(newArgc, newArgv);

    QCursor cursor(Qt::BlankCursor);
    QApplication::setOverrideCursor(cursor);
    QApplication::changeOverrideCursor(cursor);

    MainWindow *window = new MainWindow();
    window -> show();

    QDBusConnection connection = QDBusConnection::sessionBus();

    if (!connection.registerObject("/Screenly", window,  QDBusConnection::ExportAllSlots))
    {
        qWarning() << "Can't register object";
        return 1;
    }
    qDebug() << "WebView connected to D-bus";

    if (!connection.registerService("screenly.webview")) {
        qWarning() << qPrintable(QDBusConnection::sessionBus().lastError().message());
        return 1;
    }
    qDebug() << "Screenly service start";


    return app.exec();
}
