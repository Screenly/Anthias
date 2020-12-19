#include <QApplication>
#include <QDebug>
#include <QtDBus>
#include <QtWebEngine>
#include <QWebEngineView>

#include "mainwindow.h"

int main(int argc, char *argv[])
{
    QApplication app(argc, argv);

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
