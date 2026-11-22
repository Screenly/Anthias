"""Controller actions on paired screens.

Everything here talks to the network and is meant to run inside a
Celery task (see :mod:`.tasks`), never in a web request.
"""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from anthias_server.controller import vault
from anthias_server.controller.client import (
    CONNECT_TIMEOUT_S,
    NodeClient,
    NodeError,
    NodeUnauthorizedError,
)
from anthias_server.controller.health import health_from_info
from anthias_server.controller.mirror import build_manifest, sync_node
from anthias_server.controller.models import (
    AuditEntry,
    Node,
    NodeStatus,
    PublishState,
)

logger = logging.getLogger(__name__)

STATUS_READ_TIMEOUT_S = 15

COMMANDS = {
    'previous': 'Previous asset',
    'next': 'Next asset',
    'display_on': 'Display on',
    'display_off': 'Display off',
    'reboot': 'Reboot',
}
# Tabler icons for the compact per-screen buttons.
COMMAND_ICONS = {
    'previous': 'ti-player-skip-back',
    'next': 'ti-player-skip-forward',
    'display_on': 'ti-device-tv',
    'display_off': 'ti-device-tv-off',
    'reboot': 'ti-refresh',
}


def controller_enabled() -> bool:
    from anthias_server.settings import settings

    # Re-read: the toggle is saved by the web process, and this also
    # runs in the Celery worker.
    settings.load()
    return bool(settings['controller_mode']) and bool(settings['auth_backend'])


def controller_name(host: str = '') -> str:
    """The name screens show for this controller.

    The player name when set. Otherwise the address the operator used
    to reach it: inside the container ``gethostname()`` is only the
    container id, which means nothing to anyone.
    """
    from anthias_server.settings import settings

    name = str(settings['player_name'] or '').strip()
    if not name:
        name = f'Anthias at {host}' if host else 'Anthias controller'
    return name[:100]


def audit(
    action: str,
    outcome: str,
    *,
    node: Node | None = None,
    node_name: str = '',
    detail: str = '',
    actor: str = '',
) -> AuditEntry:
    entry = AuditEntry.objects.create(
        action=action,
        outcome=outcome,
        node=node,
        node_name=(node.name if node else node_name)[:100],
        detail=detail[:2000],
        actor=actor[:150],
    )
    log = logger.info if outcome == 'ok' else logger.warning
    log(
        'controller %s on %s: %s %s',
        action,
        entry.node_name or '-',
        outcome,
        detail,
    )
    return entry


def controller_client_cert() -> tuple[str, str]:
    """The client certificate this controller presents to its screens.

    Managed screens only accept calls that present it (mutual TLS; see
    ``controller.ca``). Issued, or renewed, on demand.
    """
    from anthias_server.controller import ca

    return ca.ensure_client_cert()


def client_for(node: Node) -> NodeClient:
    try:
        token = vault.decrypt(node.token_ciphertext)
    except vault.VaultError as exc:
        raise NodeUnauthorizedError(str(exc)) from exc
    return NodeClient(
        node.base_url,
        node.fingerprint,
        token,
        client_cert=controller_client_cert(),
    )


def _set_status(node: Node, status: str, detail: str = '') -> None:
    now = timezone.now()
    node.status = status
    node.status_detail = detail[:1000]
    node.last_checked_at = now
    fields = ['status', 'status_detail', 'last_checked_at']
    if status == NodeStatus.ONLINE:
        node.last_seen_at = now
        fields.append('last_seen_at')
    node.save(update_fields=fields)


def _record_error(node: Node, exc: NodeError) -> None:
    _set_status(node, exc.status, str(exc))


def remove_node(node: Node, actor: str = '') -> str:
    """Revoke our token on the screen (best effort) and forget it."""
    detail = 'token revoked on the screen'
    outcome = 'ok'
    try:
        with client_for(node) as client:
            client.unpair()
    except NodeUnauthorizedError:
        detail = 'the screen had already left management'
    except NodeError as exc:
        detail = (
            f'could not reach the screen to revoke the token ({exc}); '
            'removed here anyway. Use "Leave management" on the screen.'
        )
        outcome = 'partial'
    name = node.name
    if node.client_serial:
        from anthias_server.controller import ca

        ca.revoke(node.client_serial, name, 'removed', node.client_not_after)
        detail += '; certificate revoked'
    node.delete()
    audit('remove', outcome, node_name=name, detail=detail, actor=actor)
    return detail


def check_node(node: Node) -> Node:
    try:
        with client_for(node) as client:
            info = client.json(
                'GET',
                '/info',
                timeout=(CONNECT_TIMEOUT_S, STATUS_READ_TIMEOUT_S),
            )
    except NodeError as exc:
        _record_error(node, exc)
        return node
    info = info if isinstance(info, dict) else {}
    node.version = str(info.get('anthias_version') or '')[:100]
    node.device_model = str(info.get('device_model') or '')[:200]
    node.health = health_from_info(info)
    node.health_at = timezone.now()
    node.save(update_fields=['version', 'device_model', 'health', 'health_at'])
    _set_status(node, NodeStatus.ONLINE)
    return node


def publish(node: Node, actor: str = '') -> str:
    node.publish_state = PublishState.RUNNING
    node.publish_detail = ''
    node.save(update_fields=['publish_state', 'publish_detail'])
    manifest = build_manifest()
    try:
        with client_for(node) as client:
            result = sync_node(node, client, manifest)
    except NodeError as exc:
        _record_error(node, exc)
        node.publish_state = PublishState.FAILED
        node.publish_detail = str(exc)[:1000]
        node.save(update_fields=['publish_state', 'publish_detail'])
        audit('publish', 'failed', node=node, detail=str(exc), actor=actor)
        return node.publish_detail
    _set_status(node, NodeStatus.ONLINE)
    detail = result.summary()
    if manifest.skipped:
        detail += '. Not published: ' + ', '.join(manifest.skipped)
    node.publish_state = PublishState.OK if result.ok else PublishState.FAILED
    node.publish_detail = detail[:1000]
    node.published_at = timezone.now()
    node.save(
        update_fields=['publish_state', 'publish_detail', 'published_at']
    )
    audit(
        'publish',
        'ok' if result.ok else 'failed',
        node=node,
        detail=detail,
        actor=actor,
    )
    return detail


def run_command(node: Node, command: str, actor: str = '') -> str:
    if command not in COMMANDS:
        raise ValueError(f'Unknown command {command!r}')
    try:
        with client_for(node) as client:
            message = _dispatch(client, command)
    except NodeError as exc:
        if not getattr(exc, 'status_code', 0):
            _record_error(node, exc)
        audit(command, 'failed', node=node, detail=str(exc), actor=actor)
        return str(exc)
    _set_status(node, NodeStatus.ONLINE)
    audit(command, 'ok', node=node, detail=message, actor=actor)
    return message


def _dispatch(client: NodeClient, command: str) -> str:
    if command == 'reboot':
        client.reboot()
        return 'Reboot started.'
    if command in ('display_on', 'display_off'):
        state = command.removeprefix('display_')
        return client.display_power(state) or f'Display turned {state}.'
    client.playback(command)
    return 'Asset switched.'


def run_local_command(command: str, actor: str = '') -> tuple[bool, str]:
    """The same commands, applied to this device (controller + player)."""
    from anthias_server.celery_tasks import reboot_anthias
    from anthias_server.lib import diagnostics
    from anthias_server.settings import ViewerPublisher

    ok, message = True, ''
    if command == 'reboot':
        reboot_anthias.apply_async()
        message = 'Reboot started.'
    elif command in ('display_on', 'display_off'):
        if not diagnostics.cec_available():
            ok, message = False, 'No HDMI-CEC adapter detected.'
        else:
            ok, message = diagnostics.set_display_power(
                on=command == 'display_on'
            )
    elif command in ('next', 'previous'):
        ViewerPublisher.get_instance().send_to_viewer(command)
        message = 'Asset switched.'
    else:
        raise ValueError(f'Unknown command {command!r}')
    audit(
        command,
        'ok' if ok else 'failed',
        node_name='This screen',
        detail=message,
        actor=actor,
    )
    return ok, message


def node_summary(node: Node) -> dict[str, Any]:
    return {
        'id': node.pk,
        'name': node.name,
        'status': node.status,
        'publish_state': node.publish_state,
    }
