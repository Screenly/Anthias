from django.shortcuts import render, HttpResponse
from os import getenv
from settings import (
    CONFIGURABLE_SETTINGS,
    DEFAULTS,
    settings,
    ZmqPublisher,
)
from urllib.parse import urlparse
import logging # todo nico: remove this import if not needed
from lib.utils import (
    generate_perfect_paper_password,
    is_balena_app,
    is_demo_node,
    is_docker,
)
from .helpers import template


# @TODO: Turn this into a class-based view.
def index(request):
    player_name = settings['player_name']
    my_ip = urlparse(request.build_absolute_uri()).hostname
    is_demo = is_demo_node()
    resin_uuid = getenv("RESIN_UUID", None)

    ws_addresses = []

    if settings['use_ssl']:
        ws_addresses.append('wss://' + my_ip + '/ws/')
    else:
        ws_addresses.append('ws://' + my_ip + '/ws/')

    if resin_uuid:
        ws_addresses.append('wss://{}.resindevice.io/ws/'.format(resin_uuid))

    return template(request, 'index.html', {
        'ws_addresses': ws_addresses,
        'player_name': player_name,
        'is_demo': is_demo,
        'is_balena': is_balena_app(),
    })


# @TODO: Turn this into a class-based view.
def settings_page(request):
    context = {'flash': None}

    if request.method == 'POST':
        try:
            current_pass = request.POST.get('current-password', '')
            auth_backend = request.POST.get('auth_backend', '')

            if auth_backend != settings['auth_backend'] and settings['auth_backend']:
                if not current_pass:
                    raise ValueError("Must supply current password to change authentication method")
                if not settings.auth.check_password(current_pass):
                    raise ValueError("Incorrect current password.")

            prev_auth_backend = settings['auth_backend']
            if not current_pass and prev_auth_backend:
                current_pass_correct = None
            else:
                current_pass_correct = settings.auth_backends[prev_auth_backend].check_password(current_pass)
            next_auth_backend = settings.auth_backends[auth_backend]
            next_auth_backend.update_settings(current_pass_correct)
            settings['auth_backend'] = auth_backend

            for field, default in list(CONFIGURABLE_SETTINGS.items()):
                value = request.POST.get(field, default)

                if not value and field in ['default_duration', 'default_streaming_duration']:
                    value = str(0)
                if isinstance(default, bool):
                    value = value == 'on'

                if field == 'default_assets' and settings[field] != value:
                    if value:
                        add_default_assets()
                    else:
                        remove_default_assets()

                settings[field] = value

            settings.save()
            publisher = ZmqPublisher.get_instance()
            publisher.send_to_viewer('reload')
            context['flash'] = {'class': "success", 'message': "Settings were successfully saved."}
        except ValueError as e:
            context['flash'] = {'class': "danger", 'message': e}
        except IOError as e:
            context['flash'] = {'class': "danger", 'message': e}
        except OSError as e:
            context['flash'] = {'class': "danger", 'message': e}
    else:
        settings.load()

    for field, default in list(DEFAULTS['viewer'].items()):
        if field == 'usb_assets_key':
            if not settings[field]:
                settings[field] = generate_perfect_paper_password(20, False)
                settings.save()
        context[field] = settings[field]

    auth_backends = []

    for backend in settings.auth_backends_list:
        if backend.template:
            html, ctx = backend.template
            context.update(ctx)
        else:
            html = None
        auth_backends.append({
            'name': backend.name,
            'text': backend.display_name,
            'template': html,
            'selected': 'selected' if settings['auth_backend'] == backend.name else ''
        })

    context.update({
        'user': settings['user'],
        'need_current_password': bool(settings['auth_backend']),
        'is_balena': is_balena_app(),
        'is_docker': is_docker(),
        'auth_backend': settings['auth_backend'],
        'auth_backends': auth_backends
    })

    return template(request, 'settings.html', context)
