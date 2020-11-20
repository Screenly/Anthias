# -*- coding: utf-8 -*-

import os
import sys
import traceback
import sh

import click
import requests
import time

from requests.auth import HTTPBasicAuth

HOME = os.getenv('HOME', '/home/pi')

BASE_API_SCREENLY_URL = 'https://api.screenlyapp.com'
ASSETS_SCREENLY_OSE_API = 'http://127.0.0.1/api/v1.1/assets'

PORT_NGROK = 4040
PORT = 80

token = None
ngrok_public_url = None


################################
# Suprocesses
################################

def start_http_ngrok_process(try_connection=100):
    click.echo(click.style("Ngrok starting ...", fg='yellow'))

    sh.ngrok('http', str(PORT), _bg=True, _in=os.devnull, _out=os.devnull, _err=sys.stderr)

    try_count = 0
    while True:
        if try_count >= try_connection:
            raise Exception('Failed start ngrok')
        try:
            requests.get('http://127.0.0.1:%i' % PORT_NGROK, timeout=10)
            break
        except requests.exceptions.ConnectionError:
            try_count += 1
            time.sleep(0.1)

    click.echo(click.style("Ngrok successfull started", fg='green'))


def get_ngrock_public_url(try_connection=100):
    try_count = 0
    while True:
        if try_count >= try_connection:
            raise Exception('Could not take a public url ngrok')
        response = requests.get('http://127.0.0.1:%i/api/tunnels' % PORT_NGROK, timeout=10).json()
        if response['tunnels']:
            break
        else:
            try_count += 1
            time.sleep(0.1)
            continue
    return response['tunnels'][0]['public_url']


################################
# Utilities
################################

def progress_bar(count, total, text=''):
    """
    This simple console progress bar
    For display progress asset uploads
    """
    progress_line = "\xe2" * int(round(50 * count / float(total))) + '-' * (50 - int(round(50 * count / float(total))))
    percent = round(100.0 * count / float(total), 1)
    sys.stdout.write('[%s] %s%s %s\r' % (progress_line, percent, '%', text))
    sys.stdout.flush()


def set_token(value):
    global token
    token = 'Token %s' % value


def set_ngrok_public_url(value):
    global ngrok_public_url
    ngrok_public_url = value


################################
# Database
################################

def get_assets_by_screenly_ose_api():
    if click.confirm('Do you need authentication to access Screenly-OSE API?'):
        login = click.prompt('Login')
        password = click.prompt('Password', hide_input=True)
        auth = HTTPBasicAuth(login, password)
    else:
        auth = None
    response = requests.get(ASSETS_SCREENLY_OSE_API, timeout=10, auth=auth)
    if response.status_code == 200:
        return response.json()
    elif response.status_code == 401:
        raise Exception('Access denied')


################################
# Requests
################################

def send_asset(asset):
    endpoind_url = '%s/api/v3/assets/' % BASE_API_SCREENLY_URL
    headers = {
        'Authorization': token
    }
    asset_uri = asset['uri']
    if asset_uri.startswith(HOME):
        asset_uri = os.path.join(ngrok_public_url, asset['asset_id'])
    data = {
        'title': asset['name'],
        'source_url': asset_uri
    }
    response = requests.post(endpoind_url, data=data, headers=headers)
    return response.status_code == 200


def check_validate_token(api_key):
    endpoind_url = '%s/api/v3/assets/' % BASE_API_SCREENLY_URL
    headers = {
        'Authorization': 'Token %s' % api_key
    }
    response = requests.get(endpoind_url, headers=headers)
    if response.status_code == 200:
        return api_key
    else:
        return None


def get_api_key_by_credentials(username, password):
    endpoind_url = '%s/api/v3/tokens/' % BASE_API_SCREENLY_URL
    data = {
        'username': username,
        'password': password
    }
    response = requests.post(endpoind_url, data=data)
    if response.status_code == 200:
        return response.json()['token']
    else:
        return None


################################
################################

def start_migration():
    if click.confirm('Do you want to start assets migration?'):
        click.echo('\n')
        start_http_ngrok_process()
        set_ngrok_public_url(get_ngrock_public_url())
        click.echo('\n')
        assets_migration()


def assets_migration():
    assets = get_assets_by_screenly_ose_api()
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
              prompt='What do you want to use for migration?\n1.API token\n2.Credentials\n0.Exit\nYour choice',
              type=click.Choice(['1', '2', '0']))
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
        elif method == '0':
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
       _____                           __         ____  _____ ______
      / ___/_____________  ___  ____  / /_  __   / __ \/ ___// ____/
      \__ \/ ___/ ___/ _ \/ _ \/ __ \/ / / / /  / / / /\__ \/ __/
     ___/ / /__/ /  /  __/  __/ / / / / /_/ /  / /_/ /___/ / /___
    /____/\___/_/   \___/\___/_/ /_/_/\__, /   \____//____/_____/
                                     /____/
    """, fg='blue'))

    main()
