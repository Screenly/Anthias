TEMPLATE = app

QT += webenginecore webenginewidgets dbus
CONFIG += c++17

SOURCES += src/main.cpp \
    src/mainwindow.cpp \
    src/view.cpp

# Default rules for deployment.
include(src/deployment.pri)

HEADERS += \
    src/mainwindow.h \
    src/view.h
