## Building QT

Since our entire build environment resides inside a Docker container, you don't need to install any packages on the host system. Everything is confined to the Docker image. Do however note that as of this writing, the multi-platform support is still in beta so, you need to enable this. Instructions for how to get started with multi-platform builds can be found [here](https://medium.com/@artur.klauser/building-multi-architecture-docker-images-with-buildx-27d80f7e2408).

```
$ cd webview
$ docker build -t qt-builder .
```

You should now be able to invoke a run executing the following command:

```
$ docker run --rm -t \
    -v ~/tmp/qt-src:/src \
    -v ~/tmp/qt-build:/build \
    qt-builder
```

This will launch `build-qt.sh` and start the process of building QT for *all* Raspberry Pi boards. The resulting files will be placed in `~/tmp/qt-build/`.

### Build Arguments

You can append the following environment variables to configure the build process:

* `CLEAN_BUILD`: Set to `1` to ensure a clean build (not including the `ccache` cache).
* `BUILD_WEBVIEW`:  Set to `0` to disable the build of ScreenlyWebView.
* `TARGET`: Specify a particular target (such as `pi3` or `pi4`) instead of all existing boards.

## Usage

DBus is used for communication.
Webview registers `screenly.webview` object at `/Screenly` address on the session bus.

Webview provides 2 methods:`loadPage` and `loadImage`.

Example of interaction (python):

```
from pydbus import SessionBus

bus = SessionBus()
browser_bus = bus.get('screenly.webview', '/Screenly')

browser_bus.loadPage("www.example.com")
```

Supported protocols: `http://`, `https://`

## Debugging

You can enable QT debugging by using the following:
```
export QT_LOGGING_RULES=qt.qpa.*=true
```
