cmake_minimum_required(VERSION 3.5)

project(AnthiasWebView LANGUAGES CXX)

set(CMAKE_INCLUDE_CURRENT_DIR ON)

set(CMAKE_AUTOUIC ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(
    Qt6 COMPONENTS
    Core
    DBus
    Widgets
    WebEngineCore
    WebEngineWidgets
    REQUIRED
)

set(
    CMAKE_C_FLAGS "${CMAKE_CXX_FLAGS} -fPIC -Wl,-rpath-link, ${CMAKE_SYSROOT}/usr/lib/${CMAKE_LIBRARY_ARCHITECTURE} -L${CMAKE_SYSROOT}/usr/lib/${CMAKE_LIBRARY_ARCHITECTURE}"
)
set(
    CMAKE_CXX_FLAGS
    "${CMAKE_CXX_FLAGS} -fPIC -Wl,-rpath-link,${CMAKE_SYSROOT}/usr/lib/${CMAKE_LIBRARY_ARCHITECTURE} -L${CMAKE_SYSROOT}/usr/lib/${CMAKE_LIBRARY_ARCHITECTURE}"
)

add_executable(
    ScreenlyWebview
    src/main.cpp
    src/mainwindow.cpp
    src/view.cpp
    src/mainwindow.h
    src/view.h
)

target_link_libraries(
    ScreenlyWebview PRIVATE
    Qt6::Core
    Qt6::DBus
    Qt6::WebEngineCore
    Qt6::WebEngineWidgets
)

target_include_directories(ScreenlyWebview PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
