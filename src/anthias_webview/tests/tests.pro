# QtTest-based unit tests for AnthiasViewer's QtMultimedia
# pipeline (issue #2904). Built and run via bin/test_webview_cpp.sh
# inside a container or on a host with Qt 6 (qt6-multimedia-dev +
# qt6-declarative-dev). Not wired into the main viewer Docker image;
# the production Dockerfile only builds AnthiasViewer.pro (no test
# sources or test runner are shipped to devices).

TEMPLATE = app
TARGET = AnthiasViewerTests

QT += core gui testlib widgets multimedia quick quickwidgets dbus
CONFIG += c++17 console testcase

# Re-use the production sources verbatim — tests instantiate
# VideoView directly and call the rotation helpers. ``main.cpp`` is
# excluded because the test binary provides its own combined entry
# point (test_videoview.cpp's main() runs both TestVideoView and
# TestRotation — QTEST_MAIN only hosts one class). The qrc carries the
# QML scene (videoview.qml) the production widget loads. rotation.cpp is
# deliberately QtCore-only (no View / QtWebEngine) so these tests link
# without the webengine modules. image_fallback.cpp is the same shape:
# the black-flash fallback's paint/no-paint decision, extracted from
# View::paintEvent() so it's covered here too. image_transition.cpp
# is the same shape again: the image-to-image crossfade's env-var
# parsing and progress math, extracted from View so they're covered
# here without pulling in QtWebEngine. image_compositing.cpp is the
# one QtGui actually needs: the crossfade's pixel blend itself
# (QImage/QPainter/QRegion only, no QWidget), split out after an
# earlier CompositionMode_Plus-based version looked right by the
# progress arithmetic but produced the wrong pixels: something only
# a test that composites and reads back a colour could catch, which
# none of the above could, since none of them touch a pixel.
SOURCES += \
    ../src/videoview.cpp \
    ../src/rotation.cpp \
    ../src/image_fallback.cpp \
    ../src/image_transition.cpp \
    ../src/image_compositing.cpp \
    test_videoview.cpp \
    test_rotation.cpp \
    test_image_fallback.cpp \
    test_image_transition.cpp \
    test_image_compositing.cpp

HEADERS += \
    ../src/videoview.h \
    ../src/rotation.h \
    ../src/image_fallback.h \
    ../src/image_transition.h \
    ../src/image_compositing.h

RESOURCES += ../src/videoview.qrc

INCLUDEPATH += ../src
