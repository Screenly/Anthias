"""v2 endpoint a controller uses to check on and remove this screen.

Joining is initiated by the screen (see
:mod:`anthias_server.lib.management`). New v2 endpoint only; no
existing endpoint changes shape.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_server.app.models import ApiToken
from anthias_server.lib import management
from anthias_server.lib.auth import authorized

_DETAIL = {'type': 'object', 'properties': {'detail': {'type': 'string'}}}


class PairingViewV2(APIView):
    """Managed state, and controller-initiated unpairing."""

    @extend_schema(
        summary='Get management (pairing) state',
        responses={
            200: {
                'type': 'object',
                'properties': {
                    'managed': {'type': 'boolean'},
                    'controller_name': {'type': 'string', 'nullable': True},
                },
            }
        },
    )
    @authorized
    def get(self, request: Request) -> Response:
        controller = management.current_controller()
        return Response(
            {
                'managed': controller is not None,
                'controller_name': controller.name if controller else None,
            }
        )

    @extend_schema(
        summary='Unpair the calling controller',
        responses={204: None, 403: _DETAIL},
    )
    @authorized
    def delete(self, request: Request) -> Response:
        token = request.auth
        if not isinstance(token, ApiToken):
            return Response(
                {'detail': "Call this with the controller's bearer token."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not management.unpair_token(token.pk):
            return Response(
                {'detail': 'This token does not pair a controller.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
