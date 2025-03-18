#include "screenlyinterface.h"

ScreenlyInterface::ScreenlyInterface(QObject *parent)
    : QObject(parent)
{
    // Register DBus service and object
    QDBusConnection::sessionBus().registerService("screenly.webview");
    QDBusConnection::sessionBus().registerObject("/Screenly", this,
        QDBusConnection::ExportScriptableSlots);
}

void ScreenlyInterface::loadPage(const QString &url)
{
    emit loadPageRequested(url);
}

void ScreenlyInterface::loadImage(const QString &path)
{
    emit loadImageRequested(path);
}

bool ScreenlyInterface::isLocalFile(const QString &path) const
{
    return QFileInfo(path).isFile();
}

QString ScreenlyInterface::getAccessDeniedPage() const
{
    QString path = QStandardPaths::locate(QStandardPaths::AppDataLocation, "res/access_denied.html");
    return QString("file://%1").arg(path);
}
