#include <QApplication>
#include <QByteArray>
#include <QDebug>
#include <QtDBus>

#include "mainwindow.h"

namespace {
// Realise the operator's "Prefer dark mode" setting. The Python viewer
// plumbs the Django setting in via the ANTHIAS_PREFER_DARK_MODE env var
// (see _build_webview_env in src/anthias_viewer/__init__.py); here we
// translate that into the Chromium switch that makes QtWebEngine render
// web pages dark. Going through --blink-settings keeps one code path
// across Qt5 (Pi 1-4) and Qt6 (Pi 5/x86) without a version macro: it
// sets the same Blink runtime flag that QWebEngineSettings::ForceDarkMode
// toggles on Qt 6.7+. Dark-aware sites get their own dark theme (Chromium
// then reports prefers-color-scheme: dark) and the rest are auto-darkened.
// Must run before QApplication constructs QtWebEngine's Chromium context,
// since the switch is only read once at engine init.
void applyDarkModePreference()
{
    const QByteArray preference = qgetenv("ANTHIAS_PREFER_DARK_MODE");
    if (preference != "1" && preference != "true") {
        return;
    }

    QByteArray flags = qgetenv("QTWEBENGINE_CHROMIUM_FLAGS");

    // Idempotent: nothing to do if dark mode is already requested.
    if (flags.contains("forceDarkModeEnabled")) {
        return;
    }

    const QByteArray darkSetting = "forceDarkModeEnabled=true";
    const int blinkIdx = flags.indexOf("--blink-settings=");
    if (blinkIdx >= 0) {
        // Merge into the existing --blink-settings switch rather than
        // appending a second one: Chromium keeps only the last
        // occurrence of a given switch, so a duplicate would silently
        // drop whatever Blink settings were already configured. The
        // switch's comma-separated value runs to the next space (or the
        // end of the string).
        int valueEnd = flags.indexOf(' ', blinkIdx);
        if (valueEnd < 0) {
            valueEnd = flags.size();
        }
        flags.insert(valueEnd, "," + darkSetting);
    } else {
        if (!flags.isEmpty()) {
            flags.append(' ');
        }
        flags.append("--blink-settings=" + darkSetting);
    }
    qputenv("QTWEBENGINE_CHROMIUM_FLAGS", flags);
}
}  // namespace

int main(int argc, char *argv[])
{
    applyDarkModePreference();

    QApplication app(argc, argv);

    QApplication::setOverrideCursor(QCursor(Qt::BlankCursor));

    MainWindow *window = new MainWindow();
    // Show fullscreen exactly once, here, after the window is fully
    // constructed. Previously the MainWindow ctor also called
    // showFullScreen(), so the window was shown twice — under
    // cage/wayland that double-commit triggered wlroots' "A configure
    // is scheduled for an uninitialized xdg_surface" warning at startup.
    window->showFullScreen();

    QDBusConnection connection = QDBusConnection::sessionBus();

    // ExportAllSlots covers loadPage / loadImage / setReloadInterval /
    // playVideo / stopVideo; ExportAllSignals exposes MainWindow's
    // ``videoEnded`` signal so the Python viewer can subscribe to it
    // and learn when libmpv finishes a clip without polling (issue
    // #2904 follow-up; the current asset_loop still sleeps for
    // ``duration`` and doesn't subscribe).
    if (!connection.registerObject(
            "/Anthias", window,
            QDBusConnection::ExportAllSlots
                | QDBusConnection::ExportAllSignals))
    {
        qWarning() << "Can't register object:" << connection.lastError().message();
        return 1;
    }
    qDebug() << "WebView connected to D-bus";

    if (!connection.registerService("anthias.viewer")) {
        qWarning() << qPrintable(connection.lastError().message());
        return 1;
    }
    // NOTE: viewer/__init__.py waits for this exact line on stdout to
    // know the WebView has finished registering D-Bus and is ready for
    // loadPage/loadImage calls. Don't change the wording.
    qInfo() << "Anthias service start";

    return app.exec();
}
