#include <QApplication>
#include <QByteArray>
#include <QDebug>
#include <QtDBus>

#include <cstdio>
#include <cstdlib>
#include <csignal>
#include <cstddef>
#include <unistd.h>

// backtrace() / backtrace_symbols_fd() are glibc extensions. Every image
// we ship is Debian-based, so this is always taken in practice; the
// guard is here so the file still compiles if the webview is ever built
// against a libc without <execinfo.h>, with the handler degrading to
// naming the signal.
#if defined(__GLIBC__)
#include <execinfo.h>
#define ANTHIAS_HAVE_BACKTRACE 1
#endif

#include "mainwindow.h"

namespace {
// Fatal-signal backtrace, installed on every board.
//
// AnthiasViewer can die before its D-Bus handshake with nothing on
// stdout but Qt's benign locale/Vulkan startup chatter. The Python
// supervisor then reports a launch failure that names no cause at all —
// the shape of Sentry ANTHIAS-D, 1318 reports deep and still
// unexplained after three passes at the supervisor's own reporting.
// The supervisor spawns us with stderr merged into the stdout it
// captures and ships, so a backtrace written here reaches that report
// and names the frame that killed us.
//
// This was previously scoped to the pi3-64 build (the only one that
// links the GStreamer overlay path), where VideoView + kmssink on
// eglfs's DRM fd segfaulted silently while it was being brought up and
// docker logs showed only the respawn. That blind spot is not specific
// to pi3-64 — it is the same one ANTHIAS-D sits in on pi5 — so the
// handler is no longer gated on the build. Re-raises with the default
// handler, so the exit status, core dump and the supervisor's respawn
// behaviour are all unchanged.

// Signal name as a bare string literal. Formatting a number is not
// async-signal-safe, so a signal outside this set is simply left
// unnamed rather than rendered; the re-raise preserves the number in
// the exit status either way (and the supervisor reports it — #3361).
const char* fatalSignalName(int sig)
{
    switch (sig) {
    case SIGSEGV:
        return "SIGSEGV";
    case SIGABRT:
        return "SIGABRT";
    case SIGBUS:
        return "SIGBUS";
    case SIGFPE:
        return "SIGFPE";
    default:
        return nullptr;
    }
}

// write(2) is async-signal-safe; strlen() is not formally guaranteed to
// be, so the length is counted inline. Best-effort — losing part of the
// diagnostic to a short write or a closed stderr is not something a
// fatal handler can do anything safer about.
void writeToStderr(const char* text)
{
    size_t remaining = 0;
    while (text[remaining] != '\0') {
        ++remaining;
    }
    while (remaining > 0) {
        const ssize_t written = write(STDERR_FILENO, text, remaining);
        if (written <= 0) {
            return;
        }
        text += written;
        remaining -= static_cast<size_t>(written);
    }
}

void anthiasCrashHandler(int sig)
{
    // Kept as close to async-signal-safe as practical: write() is
    // AS-safe; backtrace()/backtrace_symbols_fd() are glibc extensions
    // that avoid malloc/stdio (unlike backtrace_symbols / fprintf) but
    // are not formally guaranteed AS-safe — acceptable for a last-gasp
    // diagnostic.
    writeToStderr("\n=== AnthiasViewer FATAL signal");
    const char* name = fatalSignalName(sig);
    if (name != nullptr) {
        writeToStderr(" ");
        writeToStderr(name);
    }
    writeToStderr(" — backtrace ===\n");
#ifdef ANTHIAS_HAVE_BACKTRACE
    void* frames[64];
    const int count = backtrace(frames, 64);
    backtrace_symbols_fd(frames, count, STDERR_FILENO);
#else
    writeToStderr("(backtrace unavailable: no <execinfo.h> in this libc)\n");
#endif
    // SA_RESETHAND (below) already restored the default disposition, so
    // re-raising re-runs the default handler (core dump / exit) without an
    // async-signal-unsafe signal() call here.
    raise(sig);
}

void installCrashHandler()
{
#ifdef ANTHIAS_HAVE_BACKTRACE
    // Prime the unwinder before any handler can need it. glibc's
    // backtrace() lazily dlopen()s libgcc on its first call and
    // allocates while doing so; reaching that from inside the fatal
    // handler can deadlock if the signal arrived while the process was
    // already inside malloc or the dynamic loader. The webview would
    // then hang instead of dying, and the Python supervisor waits out
    // the whole startup budget on a process that is never coming back —
    // the opposite of what this handler exists to do. One call here, on
    // the normal startup path, leaves the handler's call allocation-free.
    void* primer[1];
    (void)backtrace(primer, 1);
#endif

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

// Why a D-Bus call failed, in words that always say something.
//
// QDBusConnection does not guarantee it sets lastError() on a failed
// register: registerService() returning false because the name is
// already owned leaves an empty QDBusError, so the obvious
// ``qWarning() << connection.lastError().message()`` printed a blank
// line — the operator got an empty line exactly where the reason
// belonged, which is how the D-Bus failure path came to look like a
// silent death in Sentry ANTHIAS-D. Fall back to naming the most
// likely cause rather than printing nothing.
// Leave immediately with a truthful exit status.
//
// Returning normally from main() here would segfault on every
// cage/Wayland board (pi5, x86, generic arm64). QtWebEngineCore keeps a
// global QOffscreenSurface for its shared GL context and releases it in
// static destruction — after QApplication, and so after the Wayland
// platform plugin has closed the display. Its QWaylandWindow destructor
// then marshals a wl_surface::destroy onto a connection that is already
// gone:
//
//   wl_proxy_marshal_flags  <- libwayland-client
//   QtWayland::wl_surface::destroy()
//   QtWaylandClient::QWaylandWindow::~QWaylandWindow()
//   QOffscreenSurface::~QOffscreenSurface()
//   <QtWebEngineCore static teardown>
//
// (captured under gdb on a Pi 5 running 2026.9.0). The process died with
// SIGSEGV no matter why we were leaving, which matters because the
// Python supervisor reads the child's exit status to tell a genuine
// crash from a clean refusal — see _describe_webview_exit() in
// src/anthias_viewer/__init__.py and Sentry ANTHIAS-D. A startup refusal
// that reports itself as SIGSEGV sends triage hunting a crash that never
// happened.
//
// Nothing here owns unflushed state that matters: we never reached the
// event loop, so no profile data, cookies or cache have been written.
// Flush stdio — the diagnostics above are the whole point of this path —
// and skip the destructors rather than crash in them.
[[noreturn]] void failFast(int code)
{
    std::fflush(nullptr);
    std::_Exit(code);
}

// The caller supplies the last-resort guess because only it knows what
// was being registered. A name can be refused because another
// AnthiasViewer already holds it; an object path cannot — registerObject
// is local to this connection, so blaming an owner there would send
// triage after a process that has nothing to do with the failure.
QString dbusFailureReason(
    const QDBusConnection &connection, const char *fallback)
{
    const QDBusError error = connection.lastError();
    if (error.isValid() && !error.message().isEmpty()) {
        return error.message();
    }
    if (!connection.isConnected()) {
        return QStringLiteral("not connected to the session bus");
    }
    return QString::fromUtf8(fallback);
}

}  // namespace

int main(int argc, char *argv[])
{
    installCrashHandler();
    applyDarkModePreference();

    QApplication app(argc, argv);

    QApplication::setOverrideCursor(QCursor(Qt::BlankCursor));

    // Stack-allocated on purpose. This used to be a bare ``new`` that
    // was never deleted, so the window — and the QWebEnginePage inside
    // View — outlived main(). QtWebEngine's default profile is a global
    // released in static destruction, which then ran while a page still
    // referenced it: "Release of profile requested but WebEnginePage
    // still not deleted. Expect troubles !" on every exit. Declared
    // after ``app`` so it is destroyed before the QApplication, which is
    // the order Qt requires.
    MainWindow window;
    // Show fullscreen exactly once, here, after the window is fully
    // constructed. Previously the MainWindow ctor also called
    // showFullScreen(), so the window was shown twice — under
    // cage/wayland that double-commit triggered wlroots' "A configure
    // is scheduled for an uninitialized xdg_surface" warning at startup.
    window.showFullScreen();

    QDBusConnection connection = QDBusConnection::sessionBus();

    // ExportAllSlots covers loadPage / loadImage / setReloadInterval /
    // setRequestHeaders / playVideo / stopVideo; ExportAllSignals
    // exposes MainWindow's
    // ``videoEnded`` signal so the Python viewer can subscribe to it
    // and learn when libmpv finishes a clip without polling (issue
    // #2904 follow-up; the current asset_loop still sleeps for
    // ``duration`` and doesn't subscribe).
    if (!connection.registerObject(
            "/Anthias", &window,
            QDBusConnection::ExportAllSlots
                | QDBusConnection::ExportAllSignals))
    {
        qWarning() << "Can't register D-Bus object /Anthias:"
                   << qPrintable(dbusFailureReason(
                          connection, "no error reported by D-Bus"));
        failFast(1);
    }
    qDebug() << "WebView connected to D-bus";

    if (!connection.registerService("anthias.viewer")) {
        qWarning() << "Can't register D-Bus service anthias.viewer:"
                   << qPrintable(dbusFailureReason(
                          connection,
                          "no error reported by D-Bus (another "
                          "AnthiasViewer already owns the name?)"));
        failFast(1);
    }
    // NOTE: viewer/__init__.py waits for this exact line on stdout to
    // know the WebView has finished registering D-Bus and is ready for
    // loadPage/loadImage calls. Don't change the wording.
    qInfo() << "Anthias service start";

    return app.exec();
}
