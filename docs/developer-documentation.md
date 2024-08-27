# Developer documentation

## Understanding the components that make up Anthias

Here is a high-level overview of the different components that make Anthias:

![Anthias Diagram Overview](/docs/d2/anthias-diagram-overview.svg)

These components and their dependencies are mostly installed and handled with Ansible and Docker.

* The **NGINX** component (`anthias-nginx`) forwards requests to the backend and serves static files. It also acts as a reverse proxy.
* The **viewer** (`anthias-viewer`) is what drives the screen (e.g., shows web page, image or video).
* The **web app** component (`anthias-server`) &mdash; which consists of the front-end and back-end code &ndash; is what the user interacts with via browser.
* The **Celery** (`anthias-celery`) component is for aynschronouslt queueing and executing tasks outside the HTTP request-response cycle (e.g., doing assets cleanup).
* The **WebSocket** (`anthias-websocket`) component is used for forwarding requests from NGINX to the backend.
* **Redis** (`redis`) is used as a database, cache and message broker.
* The **database** component uses **SQLite** for storing the assets information.

## Dockerized development environment

To simplify development of the server module of Anthias, we've created a Docker container. This is intended to run on your local machine with the Anthias repository mounted as a volume.

Do note that Anthias is using Docker's [buildx](https://docs.docker.com/engine/reference/commandline/buildx/) for the image builds. This is used both for cross compilation as well as for local caching. You might need to run `docker buildx create --use` first.

Assuming you're in the source code repository, simply run:

```bash
$ ./bin/build_containers.sh
$ docker compose \
    -f docker-compose.dev.yml up
```

## Building containers locally

Make sure that you have `buildx` installed and that you have run
`docker buildx create --use` before you do the following:

```bash
$ ./bin/build_containers.sh
```

### Skipping specific services

Say that you would like to skip building the `anthias-viewer` and `anthias-nginx`
services. Just run the following:

```bash
$ SKIP_VIEWER=1 SKIP_NGINX=1 ./bin/build_containers.sh
```

### Generating only Dockerfiles

If you'd like to just generate the Dockerfiles from the templates provided
inside the `docker/` directory, run the following:

```bash
$ DOCKERFILES_ONLY=1 ./bin_build_containers.sh
```

## Testing
### Running the unit tests

Build and start the containers.

```bash
$ SKIP_SERVER=1 \
  SKIP_WEBSOCKET=1 \
  SKIP_NGINX=1 \
  SKIP_VIEWER=1 \
  SKIP_WIFI_CONNECT=1 \
  ./bin/build_containers.sh
$ docker compose \
    -f docker-compose.test.yml up -d
```

Run the unit tests.

```bash
$ docker compose \
    -f docker-compose.test.yml \
    exec anthias-test bash ./bin/prepare_test_environment.sh -s

# Integration and non-integration tests should be run separately as the
# former doesn't run as expected when run together with the latter.

$ docker compose \
    -f docker-compose.test.yml \
    exec anthias-test ./manage.py test --exclude-tag=integration

$ docker compose \
    -f docker-compose.test.yml \
    exec anthias-test ./manage.py test --tag=integration
```

### The QA checklist

We've also provided a [checklist](/docs/qa-checklist.md) that can serve as a guide for testing Anthias manually.

## Generating CSS and JS files

Anthias only supports compiling from the host container at the moment. You need to install the latest version
of Node.js. We recommend to intall Node.js on Linux. You can use this [guide](https://nodejs.org/en/learn/getting-started/how-to-install-nodejs)
to get started.

### Installing Node.js dependencies

Run the following command from the project root directory.

```bash
$ npm install
```

### Transpiling CSS from SASS

```bash
$ npm run sass-dev
```

### Transpiling JS from CoffeeScript

```bash
# You need to run this on a separate terminal session if you already ran the
# script for transpiling SASS files.
$ npm run coffee-dev
```

### Closing the transpiler

Just press `Ctrl-C` to close the SASS and CoffeeScript transpilers.

## Linting Python code locally

The project uses `flake8` for linting the Python codebase. While the linter is being run on the CI/CD pipeline,
you can also run it locally. There are several ways to do this.

### Run the linter using `act`

[`act`](https://nektosact.com/) lets you run GitHub Actions locally. This is useful for testing the CI/CD pipeline locally.
Installation instructions can be found [here](https://nektosact.com/installation/index.html).

After installing and setting up `act`, run the following command:

```bash
$ act -W .github/workflows/python-lint.yaml
```

The command above will run the linter on the all the Python files in the repository. If you want to run the linter
on a specific file, you can try the commands in the next section.

### Running the linter using `venv`

First, create a virtual environment and install the dependencies:

```bash
$ python3 -m venv venv/
$ source venv/bin/activate
$ pip install -r requirements/requirements.linter.txt
```

To run the linter on all the Python files in the repository, run the following command:

```bash
$ flake8 $(git ls-files '**/*.py')
```

To run the linter on a specific file, run the following command:

```bash
$ flake8 path/to/file.py
```


## Managing releases
### Creating a new release

Check what the latest release is:

```bash
$ git pull
$ git tag

# Running the `git tag` command should output something like this:
# 0.16
# ...
# v0.18.6
```

Create a new release:

```bash
$ git tag -a v0.18.7 -m "Test new automated disk images"
```

Push release:
```bash
$ git push origin v0.18.7
```

### Delete a broken release

```bash
$ git tag -d v0.18.5                         [±master ✓]
Deleted tag 'v0.18.5' (was 9b86c39)

$ git push --delete origin v0.18.5           [±master ✓]
```

## Directories and files explained

In this section, we'll explain the different directories and files that are
present in a Raspberry Pi with Anthias installed.

### `home/${USER}/screenly/`

* All of the files and folders from the Github repo should be cloned into this directory.

### `/home/${USER}/.screenly/`

* `default_assets.yml` &mdash; configuration file which contains the default assets that get added to the assets list if enabled
* `initialized` &mdash; tells whether access point service (for Wi-Fi connectivity) runs or not
* `screenly.conf` &mdash; configuration file for web interface settings
* `screenly.db` &ndash; database file containing current assets information.


### `/etc/systemd/system/`

* `wifi-connect.service` &mdash; starts the Balena `wifi-connect` program to dynamically set the Wi-Fi config on the device via the captive portal
* `anthias-host-agent.service` &mdash; starts the Python script `host_agent.py`, which subscribes from the Redis component and performs a system call to shutdown or reboot the device when the message is received.

### `/etc/sudoers.d/screenly_overrides`

* `sudoers` configuration file that allows pi user to execute certain `sudo` commands without being a superuser (i.e., `root`)

### `/usr/share/plymouth/themes/anthias`

* `anthias.plymouth` &mdash; Plymouth config file (sets module name, `ImageDir` and `ScriptFile` dir)
* `anthias.script` &ndash; plymouth script file that loads and scales the splash screen image during the boot process
* `splashscreen.png` &mdash; the spash screen image that is displayed during the boot process

## Debugging the Anthias WebView

```
export QT_LOGGING_DEBUG=1
export QT_LOGGING_RULES="*.debug=true"
export QT_QPA_EGLFS_DEBUG=1
```

The Anthias WebView is a custom-built web browser based on the [Qt](https://www.qt.io/) toolkit framework.
The browser is assembled with a Dockerfile and built by a `webview/build_qt#.sh` script.

For further info on these files and more, visit the following link: [https://github.com/Screenly/Anthias/tree/master/webview](https://github.com/Screenly/Anthias/tree/master/webview)

## Tweaking HTTP basic auth settings

* Check out [this page](/docs/http-basic-authentication.md) for more information on how to customize your basic authentication credentials.
