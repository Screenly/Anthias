#pragma once

#include <QtGlobal>
#if QT_VERSION >= QT_VERSION_CHECK(5, 7, 0)
#include <QObject>
#include <QByteArray>
#include <QWebEngineUrlRequestInfo>
#include <QWebEngineUrlRequestInterceptor>

class RequestInterceptor : public QWebEngineUrlRequestInterceptor
{
public:
    explicit RequestInterceptor(QObject* parent = nullptr);
    void interceptRequest(QWebEngineUrlRequestInfo &info) override;

private:
    QByteArray hostname;
    QByteArray version;
    QByteArray mac;
};
#endif


