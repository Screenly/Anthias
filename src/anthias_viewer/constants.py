from anthias_server.settings import LISTEN, PORT

SPLASH_DELAY = 60  # secs
EMPTY_PL_DELAY = 5  # secs

STANDBY_SCREEN = f'http://{LISTEN}:{PORT}/static/img/standby.png'
# Solid-black image shown by the ``blank`` command. On eglfs/linuxfb
# boards the Qt app owns the DRM master and can't be powered off
# externally, so painting black is how those screens "blank"; Wayland
# boards additionally DPMS-off via wlr-randr (see _apply_wlr_power).
# http is intentional and safe: the viewer reaches the local
# anthias-server over plain HTTP (TLS is the opt-in Caddy sidecar's
# job), same as STANDBY_SCREEN / SPLASH_PAGE_URL. NOSONAR (S5332).
BLACK_SCREEN = f'http://{LISTEN}:{PORT}/static/img/black.png'  # NOSONAR
SPLASH_PAGE_URL = f'http://{LISTEN}:{PORT}/splash-page'
# The WiFi setup screen, shown for as long as the wifi-connect setup
# portal is broadcasting. Same reasoning on plain http as the URLs
# above (python:S5332 false positive).
HOTSPOT_PAGE_URL = f'http://{LISTEN}:{PORT}/hotspot/'  # NOSONAR
WIFI_SETUP_STATUS_URL = (
    f'http://{LISTEN}:{PORT}/api/v2/network/wifi-setup'  # NOSONAR
)

# How often to ask the server whether the setup portal is up. The
# answer only changes when a network does, and the question costs the
# server a Redis read plus (at most, and debounced on its side) one
# host_agent refresh -- so this is paced to be unnoticeable on an idle
# device rather than to be responsive.
WIFI_SETUP_POLL_INTERVAL_S = 15

SERVER_WAIT_TIMEOUT = 60
