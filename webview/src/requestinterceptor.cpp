#include "requestinterceptor.h"

#if QT_VERSION >= QT_VERSION_CHECK(5, 6, 0)
#include <QProcessEnvironment>

RequestInterceptor::RequestInterceptor(QObject* parent)
    : QWebEngineUrlRequestInterceptor(parent)
{
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    hostname = env.value("ANTHIAS_HOSTNAME").toUtf8();
    version = env.value("ANTHIAS_VERSION").toUtf8();
    mac = env.value("ANTHIAS_MAC").toUtf8();
}

void RequestInterceptor::interceptRequest(QWebEngineUrlRequestInfo &info)
{
    if (!hostname.isEmpty()) {
        info.setHttpHeader("X-Anthias-hostname", hostname);
    }
    if (!version.isEmpty()) {
        info.setHttpHeader("X-Anthias-version", version);
    }
    if (!mac.isEmpty()) {
        info.setHttpHeader("X-Anthias-mac", mac);
    }
}
#endif


