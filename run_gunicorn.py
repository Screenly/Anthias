from typing import Any

from gunicorn.app.base import Application

from anthias_django import wsgi
from settings import LISTEN, PORT


class GunicornApplication(Application):
    def init(
        self,
        parser: Any,
        opts: Any,
        args: Any,
    ) -> dict[str, Any]:
        return {
            'bind': f'{LISTEN}:{PORT}',
            'threads': 2,
            'timeout': 20,
        }

    def load(self) -> Any:
        return wsgi.application


if __name__ == '__main__':
    GunicornApplication().run()
