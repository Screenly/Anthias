#include <QApplication>
#include <QDebug>
#include <QtDBus>

#include "mainwindow.h"

int main(int argc, char *argv[])
{
    QApplication app(argc, argv);

    QApplication::setOverrideCursor(QCursor(Qt::BlankCursor));

    MainWindow *window = new MainWindow();
    window->show();

    QDBusConnection connection = QDBusConnection::sessionBus();

    if (!connection.registerObject("/Anthias", window, QDBusConnection::ExportAllSlots))
    {
        qWarning() << "Can't register object:" << connection.lastError().message();
        return 1;
    }
    qDebug() << "WebView connected to D-bus";

    if (!connection.registerService("anthias.webview")) {
        qWarning() << qPrintable(connection.lastError().message());
        return 1;
    }
    // NOTE: viewer/__init__.py waits for this exact line on stdout to
    // know the WebView has finished registering D-Bus and is ready for
    // loadPage/loadImage calls. Don't change the wording.
    qInfo() << "Anthias service start";

    return app.exec();
}
