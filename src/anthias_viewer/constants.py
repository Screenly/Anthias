from anthias_server.settings import LISTEN, PORT

SPLASH_DELAY = 60  # secs
EMPTY_PL_DELAY = 5  # secs

STANDBY_SCREEN = f'http://{LISTEN}:{PORT}/static/img/standby.png'
SPLASH_PAGE_URL = f'http://{LISTEN}:{PORT}/splash-page'

SERVER_WAIT_TIMEOUT = 60
