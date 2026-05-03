import logging
import tarfile
import uuid
from mimetypes import guess_type
from os import path, remove

from django.contrib import messages
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from anthias_server.app import page_context
from anthias_server.celery_tasks import reboot_anthias, shutdown_anthias
from anthias_server.lib import backup_helper
from anthias_server.lib.auth import authorized
from anthias_common.utils import (
    connect_to_redis,
)
from anthias_server.settings import ViewerPublisher, settings

from .helpers import (
    add_default_assets,
    remove_default_assets,
    template,
)

logger = logging.getLogger(__name__)
r = connect_to_redis()


_ANTHIAS_REPO_URL = 'https://github.com/Screenly/Anthias'


def _checkbox(post: HttpRequest, name: str) -> bool:
    """Read a HTML checkbox: present-and-truthy = True, otherwise False.

    The settings form pairs every checkbox with a hidden 'false' input
    of the same name so unchecked boxes still POST a value. Django's
    QueryDict.getlist returns both, last wins — pick the last 'true'.
    """
    return 'true' in post.POST.getlist(name)


@authorized
def react(request: HttpRequest) -> HttpResponse:
    return template(request, 'react.html', {})


@authorized
@require_http_methods(['GET'])
def integrations(request: HttpRequest) -> HttpResponse:
    context = page_context.integrations()
    context['active_nav'] = 'integrations'
    return template(request, 'integrations.html', context)


@authorized
@require_http_methods(['GET'])
def settings_view(request: HttpRequest) -> HttpResponse:
    context = page_context.device_settings()
    context['active_nav'] = 'settings'
    return template(request, 'settings.html', context)


@authorized
@require_http_methods(['POST'])
def settings_save(request: HttpRequest) -> HttpResponse:
    """Mirror of api.views.v2.DeviceSettingsViewV2.patch.

    Touches the same settings primitives the API view does so the JSON
    and HTML write paths stay aligned without re-routing through HTTP.
    """
    settings.load()
    auth_backend = request.POST.get('auth_backend', '')
    current_password = request.POST.get('current_password', '')

    try:
        if (
            auth_backend != settings['auth_backend']
            and settings['auth_backend']
        ):
            if not current_password:
                raise ValueError(
                    'Must supply current password to change '
                    'authentication method'
                )
            auth = settings.auth
            if auth is None or not auth.check_password(current_password):
                raise ValueError('Incorrect current password.')

        prev_auth_backend = settings['auth_backend']
        if not current_password and prev_auth_backend:
            current_pass_correct = None
        else:
            current_pass_correct = settings.auth_backends[
                prev_auth_backend
            ].check_password(current_password)
        next_auth_backend = settings.auth_backends[auth_backend]
        next_auth_backend.update_settings(
            {
                'username': request.POST.get('user', ''),
                'password': request.POST.get('password', ''),
                'password_2': request.POST.get('password_2', ''),
            },
            next_auth_backend.name,
            current_pass_correct,
        )
        settings['auth_backend'] = auth_backend

        settings['player_name'] = request.POST.get('player_name', '')
        settings['default_duration'] = int(
            request.POST.get('default_duration') or 0
        )
        settings['default_streaming_duration'] = int(
            request.POST.get('default_streaming_duration') or 0
        )
        settings['audio_output'] = request.POST.get('audio_output', 'hdmi')
        settings['date_format'] = request.POST.get('date_format', 'mm/dd/yyyy')

        new_default_assets = _checkbox(request, 'default_assets')
        if new_default_assets and not settings['default_assets']:
            add_default_assets()
        elif not new_default_assets and settings['default_assets']:
            remove_default_assets()
        settings['default_assets'] = new_default_assets

        settings['show_splash'] = _checkbox(request, 'show_splash')
        settings['shuffle_playlist'] = _checkbox(request, 'shuffle_playlist')
        settings['use_24_hour_clock'] = _checkbox(request, 'use_24_hour_clock')
        settings['debug_logging'] = _checkbox(request, 'debug_logging')

        settings.save()
        ViewerPublisher.get_instance().send_to_viewer('reload')

        messages.success(request, 'Settings were successfully saved.')
    except Exception as exc:
        logger.exception('Settings save failed')
        messages.error(request, str(exc) or 'Failed to save settings.')

    return redirect(reverse('anthias_app:settings'))


@authorized
@require_http_methods(['POST'])
def settings_backup(request: HttpRequest) -> HttpResponse:
    """Same as api.views.mixins.BackupViewMixin.post but streams the
    archive back inline instead of returning the filename + relying
    on a follow-up /static_with_mime/ fetch."""
    filename = backup_helper.create_backup(name=settings['player_name'])
    archive_path = path.join('static', filename)
    response = FileResponse(
        open(archive_path, 'rb'),
        as_attachment=True,
        filename=filename,
        content_type='application/x-tgz',
    )
    return response


@authorized
@require_http_methods(['POST'])
def settings_recover(request: HttpRequest) -> HttpResponse:
    publisher = ViewerPublisher.get_instance()
    file_upload = request.FILES.get('backup_upload')
    if file_upload is None:
        messages.error(request, 'No backup file uploaded.')
        return redirect(reverse('anthias_app:settings'))

    if guess_type(file_upload.name)[0] != 'application/x-tar':
        messages.error(request, 'Incorrect file extension.')
        return redirect(reverse('anthias_app:settings'))

    # Server-side filename to defend against path traversal in the
    # client-supplied name (mirrors RecoverViewMixin).
    location = path.join('static', f'{uuid.uuid4().hex}.tar.gz')
    try:
        publisher.send_to_viewer('stop')
        with open(location, 'wb') as f:
            for chunk in file_upload.chunks():
                f.write(chunk)
        try:
            backup_helper.recover(location)
            messages.success(request, 'Recovery successful.')
        except (backup_helper.BackupRecoverError, tarfile.TarError):
            logger.exception('Backup recovery failed')
            messages.error(request, 'Invalid backup archive.')
    finally:
        if path.isfile(location):
            try:
                remove(location)
            except OSError:
                logger.exception(
                    'Failed to remove leftover backup upload at %s', location
                )
        publisher.send_to_viewer('play')

    return redirect(reverse('anthias_app:settings'))


@authorized
@require_http_methods(['POST'])
def settings_reboot(request: HttpRequest) -> HttpResponse:
    reboot_anthias.apply_async()
    messages.success(request, 'Reboot has started successfully.')
    return redirect(reverse('anthias_app:settings'))


@authorized
@require_http_methods(['POST'])
def settings_shutdown(request: HttpRequest) -> HttpResponse:
    shutdown_anthias.apply_async()
    messages.success(
        request,
        'Device shutdown has started successfully. '
        'Soon you will be able to unplug the power from your Raspberry Pi.',
    )
    return redirect(reverse('anthias_app:settings'))


@authorized
@require_http_methods(['GET'])
def system_info(request: HttpRequest) -> HttpResponse:
    context = page_context.system_info()
    # Master-branch builds get a clickable link to the commit; other
    # branches stay as plain text (mirrors AnthiasVersionValue in the
    # old React component, which only built the link when branch==master).
    version = context.get('anthias_version') or ''
    branch, _, commit = version.partition('@')
    if branch == 'master' and commit:
        context['anthias_version_master_link'] = (
            f'{_ANTHIAS_REPO_URL}/commit/{commit}'
        )
    context['active_nav'] = 'system-info'
    return template(request, 'system_info.html', context)


@require_http_methods(['GET', 'POST'])
def login(request: HttpRequest) -> HttpResponse:
    if request.method == 'POST':
        username = request.POST.get('username') or ''
        password = request.POST.get('password') or ''

        auth = settings.auth
        if (
            auth is not None
            and hasattr(auth, '_check')
            and auth._check(username, password)
        ):
            # Store credentials in session
            request.session['auth_username'] = username
            request.session['auth_password'] = password

            return redirect(reverse('anthias_app:react'))
        else:
            messages.error(request, 'Invalid username or password')
            return template(
                request, 'login.html', {'next': request.GET.get('next', '/')}
            )

    return template(
        request, 'login.html', {'next': request.GET.get('next', '/')}
    )


@require_http_methods(['GET'])
def splash_page(request: HttpRequest) -> HttpResponse:
    # IPs are populated client-side by polling /api/v2/network/ip-addresses
    # so the page renders immediately even when the host bus is slow on
    # first boot, and updates if IPs change during the splash's display
    # window (e.g. a DHCP renewal mid-splash). This also avoids the
    # historical `ipaddress.ip_address('Unknown')` crash that took the
    # whole render down on a flaky Balena supervisor.
    return template(
        request,
        'splash-page.html',
        {
            'splash_logo_url': settings['splash_logo_url'],
        },
    )
