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
# gstreamer plugin set needed. VideoView builds the render path
# from ``QMediaPlayer`` → ``QGraphicsVideoItem`` so the
# ``video-rotate`` option actually rotates the displayed frames
# (``QVideoWidget`` has no rotation property; reviewing PR #2905
# caught the prior ``setProperty("rotation", …)`` shortcut as a
# silent no-op on Pi 4).
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
    src/view.cpp

HEADERS += \
    src/mainwindow.h \
    src/view.h

greaterThan(QT_MAJOR_VERSION, 5) {
    QT += multimedia multimediawidgets
    SOURCES += src/videoview.cpp
    HEADERS += src/videoview.h
}

# Default rules for deployment.
include(src/deployment.pri)
