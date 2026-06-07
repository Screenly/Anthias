# Minimal stub covering only what
# anthias_server/lib/whitenoise.py subclasses and touches — same
# pattern as channels-stubs / redis-stubs (a future upstream py.typed
# release should replace this).
import os
from collections.abc import Callable
from typing import Any

from django.http import HttpRequest, HttpResponseBase

class WhiteNoiseMiddleware:
    files: dict[str, Any]

    def __init__(
        self,
        get_response: Callable[[HttpRequest], HttpResponseBase | None],
        settings: Any = ...,
    ) -> None: ...
    def __call__(self, request: HttpRequest) -> HttpResponseBase: ...
    def update_files_dictionary(self, root: str, prefix: str) -> None: ...
    def add_file_to_dictionary(
        self,
        url: str,
        path: str,
        stat_cache: dict[str, os.stat_result] | None = ...,
    ) -> None: ...
