from settings import LISTEN, PORT

SPLASH_DELAY = 60  # secs
EMPTY_PL_DELAY = 5  # secs

INITIALIZED_FILE = '/.screenly/initialized'

STANDBY_SCREEN = f'http://{LISTEN}:{PORT}/static/img/standby.png'
SPLASH_PAGE_URL = f'http://{LISTEN}:{PORT}/splash-page'

MAX_BALENA_IP_RETRIES = 90
BALENA_IP_RETRY_DELAY = 1
SERVER_WAIT_TIMEOUT = 60
