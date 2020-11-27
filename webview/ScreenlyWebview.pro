TEMPLATE = app

QT += webengine webenginewidgets dbus
CONFIG += c++11

SOURCES += src/main.cpp \
    src/mainwindow.cpp \
    src/view.cpp

# Additional import path used to resolve QML modules in Qt Creator's code model
QML_IMPORT_PATH =

# Default rules for deployment.
include(src/deployment.pri)

HEADERS += \
    src/mainwindow.h \
    src/view.h
