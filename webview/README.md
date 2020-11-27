## Building QT Base

Because the QT package shipped with Raspbian doens't come with all dependencies, we need to ship a separate version with the WebView.

At the moment, this build will not work with cross-compiling, and needs to be done on a Raspberry Pi 4 Model B (with 4GB RAM). You also need a few gigs of swap space and ~100GB of free space. We need to ensure that this can be cross compiled as this process takes a good day to run.

Start by building the base image:

```
$ docker build -t qt-builder .
[...]
```

With the base image done, you can build QT Base with the following command:

```
$ docker run --rm -ti \
    -v $(pwd)/build:/build -ti qt-builder
```

This will output the files in a folder called `build/` in the current directory.

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


## Cross compilation notes

Note: This is work in progress.


```
$ docker buildx build --load --platform linux/arm/v7 -t qt-builder  .
$ docker run --rm -v $(pwd):/build  qt-builder
```
