from anthias_server.settings import LISTEN, PORT

SPLASH_DELAY = 60  # secs
EMPTY_PL_DELAY = 5  # secs

STANDBY_SCREEN = f'http://{LISTEN}:{PORT}/static/img/standby.png'
# Solid-black image shown by the ``blank`` command. On eglfs/linuxfb
# boards the Qt app owns the DRM master and can't be powered off
# externally, so painting black is how those screens "blank"; Wayland
# boards additionally DPMS-off via wlr-randr (see _apply_wlr_power).
BLACK_SCREEN = f'http://{LISTEN}:{PORT}/static/img/black.png'
SPLASH_PAGE_URL = f'http://{LISTEN}:{PORT}/splash-page'

SERVER_WAIT_TIMEOUT = 60
