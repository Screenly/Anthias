#!/bin/bash

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

function install_apt_packages() {
    if [[ "$OSTYPE" == 'darwin'* ]]; then
        echo "Skipping installation of APT packages. Detected macOS."
        return
    fi

    if [[ "$OSTYPE" == 'linux'* && ! -f /etc/debian_version ]]; then
        echo "Skipping installation of APT packages. Detected non-Debian-based Linux distribution."
        return
    fi

    sudo apt-get update -y && \
    sudo apt-get install -y \
        apt-utils \
        curl \
        dialog \
        git \
        libssl-dev
}

function install_python() {
    if [[ "$OSTYPE" == 'darwin'* ]]; then
        # only install python3.11 on macOS if it's not already installed
        if brew list | grep -q python@3.11; then
            echo "Python 3.11 already installed. Skipping installation."
        else
            brew install python@3.11
        fi
    elif [[ "$OSTYPE" == 'linux'* && -f /etc/debian_version ]]; then
        sudo -E apt-get install -y \
            python3 \
            python3-pip \
            python-is-python3
    else
        echo "Skipping installation of Python. OS not supported."
    fi
}

function install_pyenv {
    if [ ! -d "$HOME/.pyenv" ]; then
        curl https://pyenv.run | bash
    fi

    export PYENV_ROOT="$HOME/.pyenv"
    [[ -d $PYENV_ROOT/bin ]] && \
        export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init -)"
}

function install_python3_11 {
    pyenv install --skip-existing 3.11
    pyenv global 3.11
}

function install_poetry() {
    curl -sSL https://install.python-poetry.org | python3 -
}

install_apt_packages
install_python
install_pyenv
install_python3_11
install_poetry
