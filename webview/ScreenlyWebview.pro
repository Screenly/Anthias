TEMPLATE = app

# Common Qt modules for both versions
QT += quick dbus

# Qt version specific configuration
equals(QT_MAJOR_VERSION, 6) {
    QT += webenginequick
    DEFINES += QT_VERSION_6
} else {
    QT += webenginecore webengine
    DEFINES += QT_VERSION_5
}

CONFIG += c++11

SOURCES += \
    src/main_qml.cpp \
    src/screenlyinterface.cpp

HEADERS += \
    src/screenlyinterface.h

RESOURCES += \
    src/qml.qrc

# Additional import path used to resolve QML modules in Qt Creator's code model
QML_IMPORT_PATH =

# Default rules for deployment.
include(src/deployment.pri)

TARGET = ScreenlyWebview
