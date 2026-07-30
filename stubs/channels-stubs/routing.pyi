from collections.abc import Iterable
from typing import Any

class ProtocolTypeRouter:
    def __init__(self, application_mapping: dict[str, Any]) -> None: ...

class URLRouter:
    def __init__(self, routes: Iterable[Any]) -> None: ...
