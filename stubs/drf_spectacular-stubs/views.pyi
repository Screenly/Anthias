from typing import Any

from django.http import HttpRequest, HttpResponse
from rest_framework.views import APIView

class SpectacularAPIView(APIView):
    def get(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponse: ...

class SpectacularYAMLAPIView(SpectacularAPIView): ...
class SpectacularJSONAPIView(SpectacularAPIView): ...

class SpectacularSwaggerView(APIView):
    def get(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponse: ...

class SpectacularSwaggerSplitView(SpectacularSwaggerView): ...

class SpectacularRedocView(APIView):
    url_name: str
    url: str | None
    template_name: str
    title: str | None
    def get(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponse: ...
