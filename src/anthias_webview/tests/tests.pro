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
# without the webengine modules.
SOURCES += \
    ../src/videoview.cpp \
    ../src/rotation.cpp \
    test_videoview.cpp \
    test_rotation.cpp

HEADERS += \
    ../src/videoview.h \
    ../src/rotation.h

RESOURCES += ../src/videoview.qrc

INCLUDEPATH += ../src
