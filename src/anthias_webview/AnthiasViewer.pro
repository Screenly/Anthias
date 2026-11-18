TEMPLATE = app

QT += webenginecore webenginewidgets dbus
CONFIG += c++17

# QtMultimedia is the in-process video pipeline (issue #2904). An
# earlier revision linked libmpv via ``mpv_render_context`` into a
# ``QOpenGLWidget``; that engaged HW decode correctly but Pi 4 V3D
# 6.0 couldn't sustain 60 fps through libmpv-render's GL upload +
# FBO + Qt compositor pipeline. Qt 6.5 dropped the upstream
# gstreamer media backend, so Debian Trixie ships only the
# ffmpeg-backed ``libffmpegmediaplugin.so``; libavcodec engages
# the v4l2_request / v4l2_m2m decoders directly via the +rpt1
# packages pinned in ``docker/_rpt1-ffmpeg-pin.j2`` — no
# gstreamer plugin set needed. VideoView renders through a QML
# ``VideoOutput`` hosted in a ``QQuickWidget`` (quickwidgets):
# frames stay on the GPU as scene-graph textures with shader
# YUV→RGB. The prior ``QMediaPlayer`` → ``QGraphicsVideoItem``
# path presented at 8–12 fps because ``QVideoFrame::toImage``
# does an RHI offscreen render + GPU→CPU readback per frame
# (issue #2967) — see videoview.h for the full history.
#
# VideoView only builds against Qt 6. The Qt 5 boards (Pi 1 /
# Pi 2 / Pi 3) route video through GstFbdevMediaPlayer on the Python side
# (see ``src/anthias_viewer/media_player.py::MediaPlayerProxy``)
# which paints straight to the framebuffer and never talks to the
# AnthiasViewer ``playVideo`` D-Bus slot — so the Qt5 build skips
# both the QtMultimedia modules and the videoview translation
# unit. QAudioDevice / QMediaPlayer pulled in by videoview.h don't
# exist in Qt 5.15.

SOURCES += src/main.cpp \
    src/mainwindow.cpp \
    src/view.cpp \
    src/rotation.cpp \
    src/image_fallback.cpp

HEADERS += \
    src/mainwindow.h \
    src/view.h \
    src/rotation.h \
    src/image_fallback.h

greaterThan(QT_MAJOR_VERSION, 5) {
    QT += multimedia quickwidgets
    SOURCES += src/videoview.cpp
    HEADERS += src/videoview.h
    RESOURCES += src/videoview.qrc
}

# pi3-64 (VideoCore IV) only: link GStreamer so VideoView can run the
# hardware pipeline (v4l2h264dec → v4l2convert/ISP → appsink) and blit
# the ISP-converted RGB frames through RasterVideoWidget. That GPU can't
# present QtMultimedia's VideoOutput (issue #3084) and its CPU is too slow
# to convert HW frames itself (~600 ms/frame), so the ISP does the
# SAND→RGB in hardware. Enabled by the pi3-64 Dockerfile with
# ``qmake6 CONFIG+=anthias_gstreamer``; every other board builds without
# it (the code is #ifdef ANTHIAS_GSTREAMER) and keeps the VideoOutput path.
anthias_gstreamer {
    CONFIG += link_pkgconfig
    PKGCONFIG += gstreamer-1.0 gstreamer-app-1.0 gstreamer-video-1.0 libdrm
    # gui-private: QPlatformNativeInterface, to read eglfs's DRM master
    # fd / CRTC / connector (nativeResourceForIntegration("dri_fd") etc.)
    # so kmssink can render onto a vc4 overlay plane using eglfs's own fd
    # — HW scanout, bypassing the GL compositor that caps at ~9 fps.
    QT += gui-private
    DEFINES += ANTHIAS_GSTREAMER
}

# -rdynamic: put AnthiasViewer's own symbols in the dynamic symbol table
# so the fatal-signal backtrace in main.cpp resolves them to names
# instead of bare addresses. Was scoped to the pi3-64 overlay build
# alongside the handler itself; the handler now runs on every board (a
# silent pre-handshake death is what Sentry ANTHIAS-D has been on pi5),
# so the symbols have to be there on every board too — a backtrace of
# unresolved addresses out of a fleet device is not something anyone can
# act on. Costs a slightly larger dynamic symbol table and nothing at
# run time; shared-library frames (Qt, Chromium, libc) resolve from
# their own tables either way.
QMAKE_LFLAGS += -rdynamic

# -funwind-tables: emit unwind information even though we don't need it
# for exceptions. aarch64 and x86-64 emit it by default, which is why a
# Pi 5 backtrace already walks the full Qt/Chromium teardown chain;
# 32-bit ARM does not, and glibc's backtrace() there gives up two frames
# in. Measured on a Pi 2 (armv7, glibc 2.41): 2 frames without, 5 with —
# the difference between "it crashed somewhere" and a stack. No runtime
# cost; it only adds .ARM.exidx / .eh_frame sections.
QMAKE_CXXFLAGS += -funwind-tables

# Default rules for deployment.
include(src/deployment.pri)
