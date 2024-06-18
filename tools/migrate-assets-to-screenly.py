# -*- coding: utf-8 -*-

import click
import os
import requests
import sys
import traceback

from inspect import cleandoc
from requests.auth import HTTPBasicAuth
from requests.exceptions import RequestException
from tenacity import retry

HOME = os.getenv('HOME')

BASE_API_SCREENLY_URL = 'https://api.screenlyapp.com'
ASSETS_ANTHIAS_API = 'http://127.0.0.1/api/v1.1/assets'

PORT = 80

token = None


#############
# Utilities #
#############

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


############
# Database #
############

def get_assets_by_anthias_api():
    if click.confirm('Do you need authentication to access Anthias API?'):
        login = click.prompt('Login')
        password = click.prompt('Password', hide_input=True)
        auth = HTTPBasicAuth(login, password)
    else:
        auth = None
    response = requests.get(ASSETS_ANTHIAS_API, timeout=10, auth=auth)

    response.raise_for_status()
    return response.json()


############
# Requests #
############

@retry
def get_post_response(endpoint_url, **kwargs):
    return requests.post(endpoint_url, **kwargs)

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

    try:
        if asset['mimetype'] in ['image', 'video']:
            post_kwargs.update({
                'files': {
                    'file': open(asset_uri, 'rb')
                }
            })
    except FileNotFoundError as error:
        click.echo(click.style('No such file or directory: %s' % error.filename, fg='red'))
        return False

    try:
        response = get_post_response(endpoint_url, **post_kwargs)
        response.raise_for_status()
    except RequestException as error:
        click.echo(click.style('Error: %s' % error, fg='red'))
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


########
# Main #
########

def start_migration():
    if click.confirm('Do you want to start assets migration?'):
        assets_migration()


def assets_migration():
    try:
        assets = get_assets_by_anthias_api()
    except RequestException as error:
        click.echo(click.style('Error: %s' % error, fg='red'))
        sys.exit(1)

    assets_length = len(assets)
    failed_assets_count = 0

    click.echo('\n')
    for index, asset in enumerate(assets):
        asset_name = str(asset['name'])
        progress_bar(index + 1, assets_length, text='Asset in migration progress: %s' % asset_name)

        status = send_asset(asset)
        if not status:
            failed_assets_count += 1
            click.echo(click.style('Failed to migrate asset: %s' % asset_name, fg='red'))

    click.echo('\n')

    if failed_assets_count > 0:
        click.echo(click.style('Migration completed with %s failed assets' % failed_assets_count, fg='red'))
    else:
        click.echo(click.style('Migration completed successfully', fg='green'))


@click.command()
@click.option(
    '--method',
    prompt=cleandoc(
        """
        What do you want to use for migration?
        1. API token
        2. Credentials (username and password)
        3. Exit
        Your choice
        """
    ),
    type=click.Choice(['1', '2', '3'])
)
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
