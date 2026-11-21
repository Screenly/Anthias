from django.urls import URLPattern, path

from anthias_server.controller.api import ControllerJoinView


def get_url_patterns() -> list[URLPattern]:
    return [
        path(
            'v2/controller/join',
            ControllerJoinView.as_view(),
            name='controller_join_v2',
        ),
    ]
