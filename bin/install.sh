#!/bin/bash -e

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

BRANCH="master"
ANSIBLE_PLAYBOOK_ARGS=()
REPOSITORY="https://github.com/Screenly/Anthias.git"
ANTHIAS_REPO_DIR="/home/${USER}/screenly"

INTRO_MESSAGE=(
    "Anthias requires a dedicated Raspberry Pi and an SD card."
    "You will not be able to use the regular desktop environment once installed."
)
MANAGE_NETWORK_PROMPT=(
    "Would you like Anthias to manage the network for you?"
)
EXPERIMENTAL_PROMPT=(
    "Would you like to install the experimental version instead?"
)
SYSTEM_UPGRADE_PROMPT=(
    "Would you like to perform a full system upgrade as well?"
)
SUDO_ARGS=()

TITLE_TEXT=$(cat <<EOF
     @@@@@@@@@
  @@@@@@@@@@@@                 d8888          888    888      d8b
 @@@@@@@  @@@    @@           d88888          888    888      Y8P
@@@@@@@@@@@@@    @@@         d88P888          888    888
@@@@@@@@@@ @@   @@@@        d88P 888 88888b.  888888 88888b.  888  8888b.  .d8888b
@@@@@       @@@@@@@@       d88P  888 888 "88b 888    888 "88b 888     "88b 88K
@@@%:      :@@@@@@@@      d88P   888 888  888 888    888  888 888 .d888888 "Y8888b.
 @@-:::::::%@@@@@@@      d8888888888 888  888 Y88b.  888  888 888 888  888      X88
  @=::::=%@@@@@@@@      d88P     888 888  888  "Y888 888  888 888 "Y888888  88888P'
     @@@@@@@@@@
EOF
)

# Install gum from Charm.sh.
# Gum helps you write shell scripts more efficiently.
# @TODO: Install a fixed version of Gum.
function install_charm_gum() {
    if [ -f /usr/bin/gum ]; then
        gum style --foreground "#FFFF00" -- \
            "Gum is already installed." | gum format
        return
    fi

    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://repo.charm.sh/apt/gpg.key | \
        sudo gpg --dearmor -o /etc/apt/keyrings/charm.gpg
    echo "deb [signed-by=/etc/apt/keyrings/charm.gpg] https://repo.charm.sh/apt/ * *" \
        | sudo tee /etc/apt/sources.list.d/charm.list
    sudo apt -y update && sudo apt -y install gum
}

function display_banner() {
    local TITLE="${1:-Anthias Installer}"
    local COLOR="212"

    gum style \
        --foreground "${COLOR}" \
        --border-foreground "${COLOR}" \
        --border "thick" \
        --margin "1 1" \
        --padding "2 6" \
        "${TITLE}"
}

function display_section() {
    local TITLE="${1:-Section}"
    local COLOR="#00FFFF"

    gum style \
        --foreground "${COLOR}" \
        --border-foreground "${COLOR}" \
        --border "thick" \
        --align center \
        --width 95 \
        --margin "1 1" \
        --padding "1 4" \
        "${TITLE}"
}

function initialize_ansible() {
    sudo mkdir -p /etc/ansible
    echo -e "[local]\nlocalhost ansible_connection=local" | \
        sudo tee /etc/ansible/hosts > /dev/null
}

function initialize_locales() {
    display_section "Initialize Locales"

    if [ ! -f /etc/locale.gen ]; then
        # No locales found. Creating locales with default UK/US setup.
        echo -e "en_GB.UTF-8 UTF-8\nen_US.UTF-8 UTF-8" | \
            sudo tee /etc/locale.gen > /dev/null
        sudo locale-gen
    fi
}

function install_packages() {
    display_section "Install Packages via APT"

    RASPBERRY_PI_OS_VERSION=$(lsb_release -rs)
    APT_INSTALL_ARGS=(
        "git"
        "libffi-dev"
        "libssl-dev"
        "whois"
    )

    if [ "$RASPBERRY_PI_OS_VERSION" -ge 12 ]; then
        APT_INSTALL_ARGS+=("python3-full")
    else
        APT_INSTALL_ARGS+=(
            "python3"
            "python3-dev"
            "python3-pip"
            "python3-venv"
        )
    fi

    if [ "$MANAGE_NETWORK" = "Yes" ]; then
        APT_INSTALL_ARGS+=("network-manager")
    fi

    sudo sed -i 's/apt.screenlyapp.com/archive.raspbian.org/g' \
        /etc/apt/sources.list
    sudo apt update -y
    sudo apt-get install -y "${APT_INSTALL_ARGS[@]}"
}

function install_ansible() {
    display_section "Install Ansible"

    GITHUB_RAW_URL="https://raw.githubusercontent.com/Screenly/Anthias"
    REQUIREMENTS_URL="$GITHUB_RAW_URL/$BRANCH/requirements/requirements.host.txt"
    ANSIBLE_VERSION=$(curl -s $REQUIREMENTS_URL | grep ansible)

    SUDO_ARGS=()

    if python3 -c "import venv" &> /dev/null; then
    gum format 'Module `venv` is detected. Activating virtual environment...'

    echo

    python3 -m venv /home/${USER}/installer_venv
    source /home/${USER}/installer_venv/bin/activate

    SUDO_ARGS+=("--preserve-env" "env" "PATH=$PATH")
    fi

    # @TODO: Remove me later. Cryptography 38.0.3 won't build at the moment.
    # See https://github.com/Screenly/Anthias/issues/1654 for details.
    sudo ${SUDO_ARGS[@]} pip install cryptography==38.0.1
    sudo ${SUDO_ARGS[@]} pip install "$ANSIBLE_VERSION"
}

function run_ansible_playbook() {
    display_section "Run the Anthias Ansible Playbook"

    sudo -u ${USER} ${SUDO_ARGS[@]} ansible localhost \
        -m git \
        -a "repo=$REPOSITORY dest=${ANTHIAS_REPO_DIR} version=${BRANCH} force=no"
    cd ${ANTHIAS_REPO_DIR}/ansible

    sudo -E -u ${USER} ${SUDO_ARGS[@]} ansible-playbook site.yml "${ANSIBLE_PLAYBOOK_ARGS[@]}"
}

function upgrade_docker_containers() {
    display_section "Initialize/Upgrade Docker Containers"

    sudo -u ${USER} ${ANTHIAS_REPO_DIR}/bin/upgrade_containers.sh
}

function cleanup() {
    display_section "Clean Up Unused Packages and Files"

    sudo apt-get autoclean
    sudo apt-get clean
    sudo docker system prune -f
    sudo apt autoremove -y
    sudo apt-get install plymouth --reinstall -y
    sudo find /usr/share/doc \
        -depth \
        -type f \
        ! -name copyright \
        -delete
    sudo find /usr/share/doc \
        -empty \
        -delete
    sudo rm -rf \
        /usr/share/man \
        /usr/share/groff \
        /usr/share/info/* \
        /usr/share/lintian \
        /usr/share/linda /var/cache/man
    sudo find /usr/share/locale \
        -type f \
        ! -name 'en' \
        ! -name 'de*' \
        ! -name 'es*' \
        ! -name 'ja*' \
        ! -name 'fr*' \
        ! -name 'zh*' \
        -delete
    sudo find /usr/share/locale \
        -mindepth 1 \
        -maxdepth 1 \
        ! -name 'en*' \
        ! -name 'de*' \
        ! -name 'es*' \
        ! -name 'ja*' \
        ! -name 'fr*' \
        ! -name 'zh*' \
        ! -name 'locale.alias' \
        -exec rm -r {} \;
}

function modify_permissions() {
    sudo chown -R ${USER}:${USER} /home/${USER}

    # Run `sudo` without entering a password.
    if [ ! -f /etc/sudoers.d/010_${USER}-nopasswd ]; then
        echo "${USER} ALL=(ALL) NOPASSWD: ALL" | \
            sudo tee /etc/sudoers.d/010_${USER}-nopasswd > /dev/null
        sudo chmod 0440 /etc/sudoers.d/010_${USER}-nopasswd
    fi
}

function write_anthias_version() {
    local GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    local GIT_SHORT_HASH=$(git rev-parse --short HEAD)
    local ANTHIAS_VERSION="Anthias Version: ${GIT_BRANCH}@${GIT_SHORT_HASH}"

    echo "${ANTHIAS_VERSION}" > ~/version.md
    echo "$(lsb_release -a 2> /dev/null)" >> ~/version.md
}

function post_installation() {
    local POST_INSTALL_MESSAGE=()
    local UPGRADE_SCRIPT_PATH="/home/${USER}/screenly/bin/upgrade_containers.sh"

    display_section "Installation Complete"

    if [ -f /var/run/reboot-required ]; then
        POST_INSTALL_MESSAGE+=(
            "Please reboot and run \`${UPGRADE_SCRIPT_PATH}\` "
            "to complete the installation."
        )
    else
        POST_INSTALL_MESSAGE+=(
            "You need to reboot the system for the installation to complete."
        )
    fi

    echo

    gum style --foreground "#00FFFF" "${POST_INSTALL_MESSAGE[@]}" | gum format

    echo

    gum confirm "Do you want to reboot now?" && \
        gum style --foreground "#FF00FF" "Rebooting..." | gum format && \
        sudo reboot
}

function main() {
    install_charm_gum && clear

    display_banner "${TITLE_TEXT}"

    gum format "${INTRO_MESSAGE[@]}"
    echo
    gum confirm "Do you still want to continue?" || exit 0
    gum confirm "${MANAGE_NETWORK_PROMPT[@]}" && \
        export MANAGE_NETWORK="Yes" || \
        export MANAGE_NETWORK="No"
    gum confirm "${EXPERIMENTAL_PROMPT[@]}" && BRANCH="experimental"
    gum confirm "${SYSTEM_UPGRADE_PROMPT[@]}" && {
        SYSTEM_UPGRADE="Yes"
        ANSIBLE_PLAYBOOK_ARGS=("--skip-tags" "system-upgrade")
    } || {
        SYSTEM_UPGRADE="No"
    }

    display_section "User Input Summary"
    gum format "**Manage Network:** ${MANAGE_NETWORK}"
    gum format "**Branch:**         \`${BRANCH}\`"
    gum format "**System Upgrade:** ${SYSTEM_UPGRADE}"

    if [ ! -d "${ANTHIAS_REPO_DIR}" ]; then
        mkdir "${ANTHIAS_REPO_DIR}"
    fi

    initialize_ansible
    initialize_locales
    install_packages
    install_ansible
    run_ansible_playbook
    upgrade_docker_containers
    cleanup
    modify_permissions

    write_anthias_version
    post_installation
}

main
