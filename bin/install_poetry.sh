#!/bin/bash

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

function install_apt_packages() {
    sudo apt-get install -y \
        apt-utils \
        curl \
        dialog \
        git \
        libssl-dev
}

function install_python() {
    sudo -E apt-get install -y \
        python3 \
        python3-pip \
        python-is-python3
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

sudo apt-get update -y
install_apt_packages
install_python
install_pyenv
install_python3_11
install_poetry
