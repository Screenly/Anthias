"""``POST /api/v2/controller/join``: a screen asks to be managed.

Reachable only on the m2m listener (Caddy's ``:8443`` site sets
``X-Anthias-Listener: m2m`` and strips it everywhere else) and only
while controller mode is on. It is unauthenticated apart from the join
secret, so DRF authentication is switched off: the screen's own bearer
token in the body is for calling the screen back, not for us.
"""

from __future__ import annotations

import json
import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from anthias_server.controller import join, service
from anthias_server.controller.mode import controller_enabled
from anthias_server.lib import management

logger = logging.getLogger(__name__)


class ControllerJoinView(APIView):
    authentication_classes = ()
    permission_classes = ()

    @extend_schema(
        summary='Join this controller (screen to controller)',
        description=(
            'Called by a screen after the operator pastes this '
            "controller's join information. Only served on the "
            'management port while controller mode is on.'
        ),
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'secret': {'type': 'string'},
                    'token': {'type': 'string'},
                    'screen_name': {'type': 'string'},
                    'screen_fp': {'type': 'string'},
                    'screen_port': {'type': 'integer'},
                    'screen_host': {'type': 'string'},
                    'screen_cert': {'type': 'string'},
                    'csr': {'type': 'string'},
                },
                'required': [
                    'secret',
                    'token',
                    'screen_fp',
                    'screen_cert',
                    'csr',
                ],
            }
        },
        responses={
            202: {
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'enum': ['pending']},
                    'request_id': {'type': 'string'},
                    'check_code': {'type': 'string'},
                    'controller_name': {'type': 'string'},
                    'controller_certificate': {'type': 'string'},
                    'expires_at': {'type': 'string', 'format': 'date-time'},
                },
            }
        },
    )
    def post(self, request: Request) -> Response:
        if not management.request_via_m2m(request) or not controller_enabled():
            return Response(
                {'detail': 'This device is not accepting screens.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        client_ip = request.META.get('REMOTE_ADDR', '')
        if join.rate_limited(client_ip):
            return Response(
                {
                    'detail': 'Too many join attempts. Wait a minute and try '
                    'again.'
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        # Refuse an oversized body before anything parses it. Caddy caps
        # it at the edge too; this covers a request that reaches the
        # server some other way, and a missing or lying length.
        try:
            length = int(request.META.get('CONTENT_LENGTH') or 0)
        except ValueError:
            length = 0
        if length > management.JOIN_MAX_BODY_BYTES:
            return Response(
                {'detail': 'The join request is too large.'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        try:
            body = request.stream.read(management.JOIN_MAX_BODY_BYTES + 1)
        except OSError:
            body = b''
        if len(body) > management.JOIN_MAX_BODY_BYTES:
            return Response(
                {'detail': 'The join request is too large.'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        try:
            data = json.loads(body or b'null')
        except (ValueError, UnicodeDecodeError):
            data = None
        # Requests without a valid secret are not audited: anyone on the
        # network can send them, and each would otherwise add a row.
        try:
            join_request = join.parse_join_request(data, client_ip)
        except join.JoinError as exc:
            return Response({'detail': exc.detail}, status=exc.status)
        try:
            pending, created = join.request_join(join_request)
        except join.JoinError as exc:
            if exc.status != status.HTTP_403_FORBIDDEN:
                service.audit(
                    'join',
                    'failed',
                    node_name=client_ip,
                    detail=exc.detail,
                )
            return Response({'detail': exc.detail}, status=exc.status)
        service.audit(
            'join request',
            'ok',
            node_name=pending.name,
            detail=(
                f'{"asked" if created else "asked again"} to join from '
                f'{pending.host}, check code '
                f'{pending.check_code}; waiting for approval'
            ),
        )
        return Response(
            {
                'status': 'pending',
                'request_id': pending.request_id,
                'check_code': pending.check_code,
                'controller_name': service.controller_name(),
                # Its key is the pin the screen keeps for this
                # controller's client certificate.
                'controller_certificate': join.controller_certificate(),
                'expires_at': pending.expires_at.isoformat(),
            },
            status=status.HTTP_202_ACCEPTED,
        )
