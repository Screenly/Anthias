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
viewer image build:

* Qt 6 boards (`pi4-64`, `pi5`, `x86`) — Debian's `qt6-base-dev` +
  `qt6-webengine-dev` packages, `qmake6 && make && make install`,
  inline in `docker/Dockerfile.viewer.j2`.
* Qt 5 boards (`pi2`, `pi3`) — cross-compiled against a pre-built
  Qt 5 toolchain, all in
  `docker/Dockerfile.qt5-webview-builder.j2`. Qt 5 is frozen for
  these boards, so the toolchain itself is a permanent artifact at
  the `WebView-v2026.04.1` GitHub release.

To rebuild a viewer image (which rebuilds the binary):

```bash
uv run python -m tools.image_builder \
    --service viewer --build-target <board>
```

## Qt 5 toolchain rebuilds (rare)

If the Qt 5 toolchain itself needs to change (CVE patch, base image
bump), `bin/rebuild_qt5_toolchain.sh` produces fresh
`qt5-5.15.14-trixie-{pi2,pi3}.tar.gz` tarballs. Re-uploading them to
the same `WebView-v2026.04.1` release tag means no source change is
needed elsewhere; uploading to a new tag means bumping
`qt5_toolchain_url` in `tools/image_builder/utils.py`. Runtime is
~2-4 hours per board on a beefy x86 host (Qt 5 + QtWebEngine under
qemu-arm); see the script header for memory caveats.

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
