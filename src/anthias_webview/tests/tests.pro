# QtTest-based unit tests for AnthiasViewer's QtMultimedia
# pipeline (issue #2904). Built and run via bin/test_webview_cpp.sh
# inside a container or on a host with Qt 6 (qt6-multimedia-dev).
# Not wired into the main viewer Docker image; the production
# Dockerfile only builds AnthiasViewer.pro (no test sources or
# test runner are shipped to devices).

TEMPLATE = app
TARGET = AnthiasViewerTests

QT += core gui testlib widgets multimedia multimediawidgets dbus
CONFIG += c++17 console testcase

# Re-use the production sources verbatim — tests instantiate
# VideoView directly. ``main.cpp`` is excluded because QTEST_MAIN
# provides its own entry point.
SOURCES += \
    ../src/videoview.cpp \
    test_videoview.cpp

HEADERS += ../src/videoview.h

INCLUDEPATH += ../src
