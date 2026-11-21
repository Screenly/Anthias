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


class PairingCertificateViewV2(APIView):
    """The controller delivers this screen's approved certificate."""

    @extend_schema(
        summary='Deliver the approved client certificate (controller)',
        description=(
            'Called by the controller once the operator approves this '
            "screen's join request, with the bearer token from that "
            'request, on the management port only.'
        ),
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'certificate': {'type': 'string'},
                    'ca_certificate': {'type': 'string'},
                    'crl': {'type': 'string'},
                    'controller_name': {'type': 'string'},
                },
                'required': ['certificate', 'ca_certificate', 'crl'],
            }
        },
        responses={204: None, 400: _DETAIL, 403: _DETAIL, 404: _DETAIL},
    )
    @authorized
    def post(self, request: Request) -> Response:
        if not management.request_via_m2m(request):
            return Response(
                {'detail': 'Not available on this port.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        token = request.auth
        if not isinstance(token, ApiToken):
            return Response(
                {'detail': "Call this with the controller's bearer token."},
                status=status.HTTP_403_FORBIDDEN,
            )
        data = request.data if isinstance(request.data, dict) else {}

        def field(name: str) -> str:
            value = data.get(name, '')
            return value if isinstance(value, str) else ''

        try:
            management.install_approval(
                token.pk,
                field('certificate'),
                field('ca_certificate'),
                field('crl'),
                field('controller_name'),
            )
        except management.ManagementError as exc:
            return Response(
                {'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
