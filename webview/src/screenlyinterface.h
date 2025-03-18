#ifndef SCREENLYINTERFACE_H
#define SCREENLYINTERFACE_H

#include <QObject>
#include <QtDBus>
#include <QFileInfo>
#include <QStandardPaths>

class ScreenlyInterface : public QObject
{
    Q_OBJECT

public:
    explicit ScreenlyInterface(QObject *parent = nullptr);

    Q_INVOKABLE bool isLocalFile(const QString &path) const;
    Q_INVOKABLE QString getAccessDeniedPage() const;

public slots:
    Q_SCRIPTABLE void loadPage(const QString &url);
    Q_SCRIPTABLE void loadImage(const QString &path);

signals:
    void loadPageRequested(const QString &url);
    void loadImageRequested(const QString &path);
};

#endif // SCREENLYINTERFACE_H
