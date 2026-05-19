# AnthiasViewer

The Qt-based browser that the viewer service launches on each
display. The host viewer binds it via D-Bus (`anthias.viewer` at
`/Anthias`) to load pages, images, and arm a per-asset auto-refresh
timer.

> Renamed from `AnthiasWebview` / `anthias.webview` in GH #2906
> Phase 4. The source directory is still `src/anthias_webview/`;
> it'll move to `src/anthias_viewer/` once the Python viewer
> package is deleted (Phase 5).

## Build flow

The viewer binary is **compiled inside the viewer image** as a
multi-stage build — there is no separate release tag, no
curl-from-releases step, no version pin to bump. Editing source
under `src/anthias_webview/src/` triggers a rebuild on the next
viewer image build.

Every board uses the same Qt 6 path: Debian Trixie's `qt6-base-dev`
+ `qt6-multimedia-dev` + `qt6-webengine-dev` packages, `qmake6 &&
make && make install`, inline in `docker/Dockerfile.viewer.j2`. Pi 2
(armhf) installs the same packages from Trixie's armhf apt tree;
every other board is arm64 or amd64. The Qt 5 cross-compile path
that previously served pi2 / pi3 was deleted in #2906 Phase 2 (pi3
also moved to arm64 in that change).

To rebuild a viewer image (which rebuilds the binary):

```bash
uv run python -m tools.image_builder \
    --service viewer --build-target <board>
```

## D-Bus API

The binary registers `anthias.viewer` at `/Anthias` on the session
bus and exposes three methods:

* `loadPage(url)` — load an HTTP(S) URL.
* `loadImage(path)` — render a local image asset.
* `setReloadInterval(seconds)` — arm an auto-refresh timer that
  reloads the visible page every `seconds` seconds (`0` disables).
  The timer clears automatically on the next `loadPage`/`loadImage`,
  so the caller arms it once after each load and the webview forgets
  it on the next rotation.

Example:

```python
from pydbus import SessionBus

bus = SessionBus()
browser_bus = bus.get('anthias.viewer', '/Anthias')

browser_bus.loadPage("https://www.example.com")
browser_bus.setReloadInterval(30)  # reload every 30s; 0 disables
```

## Debugging

Enable Qt debug logging at runtime:

```bash
export QT_LOGGING_RULES=qt.qpa.*=true
```
