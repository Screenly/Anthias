"""Screens page: list joined screens, publish, bulk controls."""

from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.http.request import split_domain_port
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from anthias_server.app.helpers import template
from anthias_server.controller import join, service, tasks
from anthias_server.controller.client import NodeError
from anthias_server.controller.mirror import build_manifest
from anthias_server.controller.models import (
    AuditEntry,
    Node,
    NodeStatus,
    PublishState,
)
from anthias_server.lib import management
from anthias_server.lib.auth import authorized

logger = logging.getLogger(__name__)

AUDIT_ROWS = 30
SELF_TARGET = 'self'
ALL_TARGET = 'all'


def _actor(request: HttpRequest) -> str:
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return str(user.get_username())
    return ''


def _snippet_host(request: HttpRequest) -> str:
    host, _port = split_domain_port(request.get_host())
    return host.strip('[]') or 'localhost'


def _nodes_context() -> dict[str, Any]:
    nodes = list(Node.objects.all())
    return {
        'nodes': nodes,
        'node_count': len(nodes),
        'online_count': sum(n.status == NodeStatus.ONLINE for n in nodes),
        'audit_entries': list(AuditEntry.objects.all()[:AUDIT_ROWS]),
        'commands': service.COMMANDS,
        'command_buttons': [
            (command, label, service.COMMAND_ICONS[command])
            for command, label in service.COMMANDS.items()
        ],
    }


def _page_context(request: HttpRequest) -> dict[str, Any]:
    from anthias_server.settings import settings

    context: dict[str, Any] = {
        'active_nav': 'screens',
        'controller_mode': bool(settings['controller_mode']),
        'auth_enabled': bool(settings['auth_backend']),
        'controller_enabled': service.controller_enabled(),
    }
    if not context['controller_enabled']:
        return context
    try:
        host = _snippet_host(request)
        context['join_snippet'] = join.build_snippet(
            host, service.controller_name(host)
        )
    except management.ManagementError as exc:
        logger.warning('Join information unavailable: %s', exc)
        context['join_error'] = (
            f'Join information is not available yet. {exc} Check the '
            'server log.'
        )
    manifest = build_manifest()
    context['manifest_count'] = len(manifest.items)
    context['manifest_skipped'] = manifest.skipped
    context.update(_nodes_context())
    return context


@authorized
@require_http_methods(['GET'])
def screens(request: HttpRequest) -> HttpResponse:
    return template(request, 'controller/screens.html', _page_context(request))


@authorized
@require_http_methods(['GET'])
def screens_table(request: HttpRequest) -> HttpResponse:
    if not service.controller_enabled():
        return HttpResponse(status=204)
    return template(
        request, 'controller/_screens_table.html', _nodes_context()
    )


def _back(request: HttpRequest) -> HttpResponse:
    return redirect(reverse('controller:screens'))


def _require_enabled(request: HttpRequest) -> HttpResponse | None:
    if service.controller_enabled():
        return None
    messages.error(request, 'Controller mode is off.')
    return _back(request)


def _targets(target: str) -> list[Node]:
    nodes = Node.objects.exclude(status=NodeStatus.UNAUTHORIZED)
    if target == ALL_TARGET:
        return list(nodes)
    if target.isdigit():
        return list(nodes.filter(pk=int(target)))
    return []


@authorized
@require_http_methods(['POST'])
def screens_publish(request: HttpRequest) -> HttpResponse:
    if (refusal := _require_enabled(request)) is not None:
        return refusal
    nodes = _targets(request.POST.get('target', ALL_TARGET))
    if not nodes:
        messages.error(request, 'There are no screens to publish to.')
        return _back(request)
    actor = _actor(request)
    Node.objects.filter(pk__in=[n.pk for n in nodes]).update(
        publish_state=PublishState.QUEUED, publish_detail=''
    )
    for node in nodes:
        tasks.publish_to_node.delay(node.pk, actor)
    messages.success(
        request,
        f'Publishing to {len(nodes)} screen{"s" if len(nodes) != 1 else ""}. '
        'Progress shows in the table.',
    )
    return _back(request)


@authorized
@require_http_methods(['POST'])
def screens_command(request: HttpRequest) -> HttpResponse:
    if (refusal := _require_enabled(request)) is not None:
        return refusal
    command = request.POST.get('command', '')
    target = request.POST.get('target', ALL_TARGET)
    if command not in service.COMMANDS:
        messages.error(request, 'Unknown command.')
        return _back(request)
    label = service.COMMANDS[command]
    actor = _actor(request)

    if target == SELF_TARGET:
        ok, message = service.run_local_command(command, actor)
        (messages.success if ok else messages.error)(
            request, f'This screen: {message}'
        )
        return _back(request)

    nodes = _targets(target)
    if not nodes:
        messages.error(request, 'There are no screens to send that to.')
        return _back(request)
    for node in nodes:
        tasks.node_command.delay(node.pk, command, actor)
    # "All screens" includes this one too when it is a player, except
    # for a reboot: rebooting the controller in the middle of a fan-out
    # would drop the commands still queued for the other screens.
    if target == ALL_TARGET and command != 'reboot':
        service.run_local_command(command, actor)
    messages.success(
        request,
        f'{label}: sent to {len(nodes)} screen'
        f'{"s" if len(nodes) != 1 else ""}. Results are in the activity '
        'log.',
    )
    return _back(request)


@authorized
@require_http_methods(['POST'])
def screens_refresh(request: HttpRequest) -> HttpResponse:
    if (refusal := _require_enabled(request)) is not None:
        return refusal
    for node in Node.objects.all():
        tasks.refresh_node.delay(node.pk)
    messages.success(request, 'Checking every screen now.')
    return _back(request)


@authorized
@require_http_methods(['POST'])
def screens_remove(request: HttpRequest, node_id: int) -> HttpResponse:
    node = get_object_or_404(Node, pk=node_id)
    name = node.name
    try:
        detail = service.remove_node(node, _actor(request))
    except NodeError as exc:
        messages.error(request, f'Could not remove {name}: {exc}')
        return _back(request)
    messages.success(request, f'Removed {name}: {detail}.')
    return _back(request)


@authorized
@require_http_methods(['POST'])
def screens_regenerate(request: HttpRequest) -> HttpResponse:
    if (refusal := _require_enabled(request)) is not None:
        return refusal
    join.regenerate_secret()
    service.audit(
        'regenerate',
        'ok',
        node_name='-',
        detail='join information regenerated; old snippets stop working',
        actor=_actor(request),
    )
    messages.success(
        request,
        'New join information created. Screens that already joined are '
        'not affected.',
    )
    return _back(request)
