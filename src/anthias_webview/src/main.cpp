#include <QApplication>
#include <QByteArray>
#include <QDebug>
#include <QtDBus>

#ifdef ANTHIAS_GSTREAMER
#include <csignal>
#include <execinfo.h>
#include <unistd.h>
#endif

#include "mainwindow.h"

namespace {
#ifdef ANTHIAS_GSTREAMER
// Fatal-signal backtrace. Scoped to the pi3-64 build (the only one that
// links the GStreamer overlay path): VideoView + kmssink on eglfs's DRM
// fd segfaulted silently while it was being brought up — the kernel
// killed the process and docker logs showed only the respawn. Dumping a
// backtrace to stderr (captured by the start_viewer wrapper) pins the
// crash frame. Re-raises with the default handler so the core dump / exit
// status are unchanged and the Python supervisor still respawns as
// before. Other boards keep the default crash behaviour untouched.
void anthiasCrashHandler(int sig)
{
    // Best-effort, kept as close to async-signal-safe as practical:
    // write() is AS-safe; backtrace()/backtrace_symbols_fd() are glibc
    // extensions that avoid malloc/stdio (unlike backtrace_symbols /
    // fprintf) but are not formally guaranteed AS-safe — acceptable for a
    // last-gasp diagnostic. The signal number is omitted from the header
    // (formatting it isn't AS-safe); the re-raise below preserves it in
    // the exit status / core dump.
    void* frames[64];
    const int count = backtrace(frames, 64);
    static const char hdr[] =
        "\n=== AnthiasViewer FATAL signal — backtrace ===\n";
    if (write(STDERR_FILENO, hdr, sizeof(hdr) - 1) < 0) {
        // Nothing safe to do if stderr is gone; fall through to re-raise.
    }
    backtrace_symbols_fd(frames, count, STDERR_FILENO);
    // SA_RESETHAND (below) already restored the default disposition, so
    // re-raising re-runs the default handler (core dump / exit) without an
    // async-signal-unsafe signal() call here.
    raise(sig);
}

void installCrashHandler()
{
    struct sigaction sa;
    sigemptyset(&sa.sa_mask);
    sa.sa_handler = anthiasCrashHandler;
    // SA_RESETHAND: reset to the default handler once ours fires, so the
    // re-raise takes the default path and we never call the
    // non-async-signal-safe signal() from within the handler.
    sa.sa_flags = SA_RESETHAND;
    sigaction(SIGSEGV, &sa, nullptr);
    sigaction(SIGABRT, &sa, nullptr);
    sigaction(SIGBUS, &sa, nullptr);
    sigaction(SIGFPE, &sa, nullptr);
}
#else
// No-op on non-pi3-64 builds — leave the platform default crash handling
// (core dumps) in place.
void installCrashHandler() {}
#endif

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
    installCrashHandler();
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
    // setRequestHeaders / playVideo / stopVideo; ExportAllSignals
    // exposes MainWindow's
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
