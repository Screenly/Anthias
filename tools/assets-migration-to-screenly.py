# -*- coding: utf-8 -*-

from __future__ import unicode_literals
from builtins import str
import os
import sys
import traceback
import sh

import click
import requests
import time

from requests.auth import HTTPBasicAuth

HOME = os.getenv('HOME')

BASE_API_SCREENLY_URL = 'https://api.screenlyapp.com'
ASSETS_ANTHIAS_API = 'http://127.0.0.1/api/v1.1/assets'

PORT = 80

token = None


################################
# Suprocesses
################################


################################
# Utilities
################################

def progress_bar(count, total, text=''):
    """
    This simple console progress bar
    For display progress asset uploads
    """
    progress_line = "\u2588" * int(round(50 * count / float(total))) + '-' * (50 - int(round(50 * count / float(total))))
    percent = round(100.0 * count / float(total), 1)
    sys.stdout.write('[%s] %s%s %s\r' % (progress_line, percent, '%', text))
    sys.stdout.flush()


def set_token(value):
    global token
    token = 'Token %s' % value


################################
# Database
################################

def get_assets_by_anthias_api():
    if click.confirm('Do you need authentication to access Anthias API?'):
        login = click.prompt('Login')
        password = click.prompt('Password', hide_input=True)
        auth = HTTPBasicAuth(login, password)
    else:
        auth = None
    response = requests.get(ASSETS_ANTHIAS_API, timeout=10, auth=auth)
    if response.status_code == 200:
        return response.json()
    elif response.status_code == 401:
        raise Exception('Access denied')


################################
# Requests
################################

def send_asset(asset):
    endpoint_url = '%s/api/v3/assets/' % BASE_API_SCREENLY_URL
    headers = {
        'Authorization': token
    }
    asset_uri = asset['uri']

    if asset_uri.startswith('/data'):
        asset_uri = os.path.join(HOME, 'screenly_assets', os.path.basename(asset_uri))

    data = {
        'title': asset['name'],
        'source_url': asset_uri
    }

    post_kwargs = {
        'data': data,
        'headers': headers,
    }

    if asset['mimetype'] in ['image', 'video']:
        post_kwargs.update({
            'files': {
                'file': open(asset_uri, 'rb') # @TODO: Add exception handling.
            }
        })

    response = requests.post(endpoint_url, **post_kwargs)

    try:
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        click.echo(click.style('Error: %s' % e, fg='red'))
        return False

    return True


def check_validate_token(api_key):
    endpoint_url = '%s/api/v3/assets/' % BASE_API_SCREENLY_URL
    headers = {
        'Authorization': 'Token %s' % api_key
    }
    response = requests.get(endpoint_url, headers=headers)
    if response.status_code == 200:
        return api_key
    else:
        return None


def get_api_key_by_credentials(username, password):
    endpoint_url = '%s/api/v3/tokens/' % BASE_API_SCREENLY_URL
    data = {
        'username': username,
        'password': password
    }
    response = requests.post(endpoint_url, data=data)
    if response.status_code == 200:
        return response.json()['token']
    else:
        return None


################################
################################

def start_migration():
    if click.confirm('Do you want to start assets migration?'):
        assets_migration()


def assets_migration():
    assets = get_assets_by_anthias_api()
    assets_length = len(assets)

    click.echo('\n')
    for index, asset in enumerate(assets):
        asset_name = str(asset['name'])
        progress_bar(index + 1, assets_length, text='Asset in migration progress: %s' % asset_name)

        status = send_asset(asset)
        if not status:
            click.echo(click.style('\n%s asset was failed migration' % asset_name, fg='red'))
    click.echo('\n')
    click.echo(click.style('Migration completed successfully', fg='green'))


@click.command()
@click.option('--method',
              prompt='What do you want to use for migration?\n1.API token\n2.Credentials\n3.Exit\nYour choice',
              type=click.Choice(['1', '2', '3']))
def main(method):
    try:
        valid_token = None

        if method == '1':
            api_key = click.prompt('Your API key')
            valid_token = check_validate_token(api_key)
        elif method == '2':
            username = click.prompt('Your username')
            password = click.prompt('Your password', hide_input=True)
            valid_token = get_api_key_by_credentials(username, password)
        elif method == '3':
            sys.exit(0)

        if valid_token:
            set_token(valid_token)
            click.echo(click.style('Successfull authentication', fg='green'))
            start_migration()
        else:
            click.echo(click.style('Failed authentication', fg='red'))
    except Exception:
        traceback.print_exc()


if __name__ == '__main__':
    click.echo(click.style("""
           d8888            888     888
          d88888            888     888       888
         d88P888            888     888
        d88P 888  88888b.   888888  88888b.   888   8888b.   .d8888b
       d88P  888  888 '88b  888     888 '88b  888      '88b  88K
      d88P   888  888  888  888     888  888  888  .d888888  'Y8888b.
     d8888888888  888  888  Y88b.   888  888  888  888  888       X88
    d88P     888  888  888   Y888   888  888  888  'Y888888   88888P'
    ==================================================================
    """, fg='cyan'))

    main()
