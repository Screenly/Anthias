## Building Qt and WebView

> [!WARNING]
> To build this, you need **very** beefy hardware. We are building this on a VM with 32 vCPUs and 128GB RAM. If you're trying to build it locally, you likely need to tweak [MAKE_CORES](https://github.com/Screenly/screenly-ose/blob/master/webview/build_qt5.sh#L12) to something lower, but you would still need a powerful workstation (32GB RAM minimum) to make this build.

### Building for Raspberry Pi (1-4)

Since our entire build environment resides inside a Docker container, you don't need to install any packages on the host system. Everything is confined to the Docker image. Do however note that as of this writing, the multi-platform support is still in beta so, you need to enable this. Instructions for how to get started with multi-platform builds can be found [here](https://medium.com/@artur.klauser/building-multi-architecture-docker-images-with-buildx-27d80f7e2408).

```bash
$ cd webview
$ docker buildx build \
    --load \
    --build-arg GIT_HASH=$(git rev-parse --short HEAD) \
    -t qt-builder .
```

Start the builder container with the following command:

```bash
$ docker run -itd \
    --name qt-builder-instance \
    -v ~/tmp/qt-src:/src:Z \
    -v ~/tmp/qt-build:/build:Z \
    -v $(pwd):/webview:ro \
    -e TARGET=${TARGET_PLATFORM} \
    qt-builder
```

You should now be able to invoke a run executing the following command:

```bash
$ docker exec -it qt-builder-instance /webview/build_webview_with_qt5.sh
```

This will start the process of building QT for *all* Raspberry Pi boards if you don't specify a `TARGET` environment variable.
The resulting files will be placed in `~/tmp/qt-build/`.

When you're done, you can stop and remove the container with the following commands:

```bash
$ docker stop qt-builder-instance
$ docker rm qt-builder-instance
```

You can learn more about this process in the blog post [Compiling Qt with Docker multi-stage and multi-platform](https://www.docker.com/blog/compiling-qt-with-docker-multi-stage-and-multi-platform/).

#### Build Arguments

You can append the following environment variables to configure the build process:

* `CLEAN_BUILD`: Set to `1` to ensure a clean build (not including the `ccache` cache).
* `BUILD_WEBVIEW`:  Set to `0` to disable the build of AnthiasWebView.
* `TARGET`: Specify a particular target (such as `pi3` or `pi4`) instead of all existing boards.

### Building for x86

```bash
$ cd webview/
$ export GIT_HASH=$(git rev-parse --short HEAD)
$ export COMPOSE_PROFILES=x86
$ docker compose up -d --build
$ docker compose exec builder-x86 /scripts/build_webview.sh
```

The resulting files will be placed in `~/tmp-x86/build/release`.

When you're done, you can stop and remove the container with the following commands:

```bash
$ docker compose down
```

### Building for Raspberry Pi 5

> [!NOTE]
> At this time, you can only build the WebView for Raspberry Pi 5 devices
> from a Raspberry Pi 5 device.
> You need to have the following installed and set up on your Raspberry Pi 5:
> - Docker (arm64)
> - Code editor of your choice (e.g., Visual Studio Code, Neovim, etc.)

The steps are similar to that of [building for x86](#building-for-x86),
but you need to specify the set the Docker Compose profile to `pi5`:

```bash
$ cd webview/
$ export GIT_HASH=$(git rev-parse --short HEAD)
$ export COMPOSE_PROFILES=pi5
$ docker compose up -d --build
$ docker compose exec builder-pi5 /scripts/build_webview.sh
```

The resulting files will be placed in `~/tmp-pi5/build/release`.

## Usage

DBus is used for communication.
Webview registers `anthias.webview` object at `/Anthias` address on the session bus.

Webview provides 2 methods:`loadPage` and `loadImage`.

Example of interaction (python):

```python
from pydbus import SessionBus

bus = SessionBus()
browser_bus = bus.get('anthias.webview', '/Anthias')

browser_bus.loadPage("www.example.com")
```

Supported protocols: `http://`, `https://`

## Debugging

> [!TIP]
> You can enable QT debugging by using the following:
> ```bash
> export QT_LOGGING_RULES=qt.qpa.*=true
> ```

## Creating a Release

Make sure that you are current in the `master` branch.

```bash
git checkout master
```

Create a new tag for the release. The WebView uses CalVer in
`YYYY.MM.PATCH` form, where `PATCH` is sequential within the month
(start at `0` for the first release of the month, bump to `1`, `2`, ...
for subsequent ones).

```bash
git tag -a WebView-v$(date -u +%Y.%m).0 -m "[tag message]"
```

> [!IMPORTANT]
> The tag name must start with `WebView-v` and the version must follow
> the `YYYY.MM.PATCH` CalVer format. The CI workflow strips the
> `WebView-v` prefix and passes the remaining `YYYY.MM.PATCH` to the
> build as `WEBVIEW_VERSION`, which ends up in the artifact filename
> (`webview-2026.04.0-bookworm-x86.tar.gz`).

Push the tag to the remote repository.

```bash
git push origin WebView-v2026.04.0
```

If you're using a forked repository, you need to push the tag to the upstream repository.

```bash
git push upstream WebView-v2026.04.0
```

Pushing this tag will trigger the [build-webview](https://github.com/Screenly/Anthias/actions/workflows/build-webview.yaml) workflow.
