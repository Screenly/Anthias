#!/bin/bash

set -euo pipefail

BRANCH="master"
ANSIBLE_PLAYBOOK_ARGS=()
REPOSITORY="https://github.com/Screenly/Anthias.git"
ANTHIAS_REPO_DIR="/home/${USER}/screenly"
GITHUB_API_REPO_URL="https://api.github.com/repos/Screenly/Anthias"
GITHUB_RELEASES_URL="https://github.com/Screenly/Anthias/releases"
GITHUB_RAW_URL="https://raw.githubusercontent.com/Screenly/Anthias"
DOCKER_TAG="latest"
UPGRADE_SCRIPT_PATH="${ANTHIAS_REPO_DIR}/bin/upgrade_containers.sh"
ARCHITECTURE=$(uname -m)

# Pin uv to match docker/uv-builder.j2 so host and image use the same binary.
UV_PIN_VERSION="0.9.17"

INTRO_MESSAGE=(
    "Anthias requires a dedicated Raspberry Pi and an SD card."
    "You will not be able to use the regular desktop environment once installed."
    ""
    "When prompted for the version, you can choose between the following:"
    "  - **latest:** Installs the latest version from the \`master\` branch."
    "  - **tag:** Installs a pinned version based on the tag name."
    ""
    "Take note that \`latest\` is a rolling release."
)
MANAGE_NETWORK_PROMPT=(
    "Would you like Anthias to manage the network for you?"
)
VERSION_PROMPT=(
    "Which version of Anthias would you like to install?"
)
VERSION_PROMPT_CHOICES=(
    "latest"
    "tag"
)
SYSTEM_UPGRADE_PROMPT=(
    "Would you like to perform a full system upgrade as well?"
)

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
function install_prerequisites() {
    if [ -f /usr/bin/gum ] && [ -f /usr/bin/jq ]; then
        return
    fi

    sudo apt -y update && sudo apt -y install gnupg

    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://repo.charm.sh/apt/gpg.key | \
        sudo gpg --dearmor -o /etc/apt/keyrings/charm.gpg
    echo "deb [signed-by=/etc/apt/keyrings/charm.gpg] https://repo.charm.sh/apt/ * *" \
        | sudo tee /etc/apt/sources.list.d/charm.list

    sudo apt -y update && sudo apt -y install gum jq
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

    local APT_INSTALL_ARGS=(
        "ca-certificates"
        "curl"
        "git"
        "libffi-dev"
        "libssl-dev"
        "whois"
    )

    if [ "$MANAGE_NETWORK" = "Yes" ]; then
        APT_INSTALL_ARGS+=("network-manager")
    fi

    if [ "$ARCHITECTURE" != "x86_64" ]; then
        sudo sed -i 's/apt.screenlyapp.com/archive.raspbian.org/g' \
            /etc/apt/sources.list
    fi

    sudo apt-get update
    sudo apt-get install -y "${APT_INSTALL_ARGS[@]}"
}

function clone_repo() {
    display_section "Clone Anthias Repository"

    if [ ! -d "${ANTHIAS_REPO_DIR}/.git" ]; then
        git clone "${REPOSITORY}" "${ANTHIAS_REPO_DIR}"
    fi
    git -C "${ANTHIAS_REPO_DIR}" fetch --tags origin
    git -C "${ANTHIAS_REPO_DIR}" checkout "${BRANCH}"
    git -C "${ANTHIAS_REPO_DIR}" reset --hard "origin/${BRANCH}"
}

function install_ansible() {
    display_section "Install uv and host Python dependencies"

    # uv manages its own Python and packages — no system pip/venv needed.
    # Pinned via UV_PIN_VERSION so the host and the docker uv-builder
    # stage stay in lockstep (see docker/uv-builder.j2).
    if ! command -v uv &> /dev/null || \
        [ "$(uv --version 2>/dev/null | awk '{print $2}')" != "${UV_PIN_VERSION}" ]; then
        curl -LsSf "https://astral.sh/uv/${UV_PIN_VERSION}/install.sh" | sh
    fi
    export PATH="$HOME/.local/bin:$PATH"

    # On upgrade from a pre-uv install, replace any installer_venv that
    # was built by `python3 -m venv` so uv can take it over cleanly.
    local INSTALLER_VENV="/home/${USER}/installer_venv"
    if [ -d "${INSTALLER_VENV}" ] && \
        ! grep -q '^uv = ' "${INSTALLER_VENV}/pyvenv.cfg" 2>/dev/null; then
        rm -rf "${INSTALLER_VENV}"
    fi

    # Resolve and install the `host` dependency group from pyproject.toml.
    # uv will fetch a compatible Python automatically (the project requires
    # >=3.11), so this works on Debian 11 too.
    UV_PROJECT_ENVIRONMENT="${INSTALLER_VENV}" \
        uv sync \
            --project "${ANTHIAS_REPO_DIR}" \
            --no-default-groups \
            --group host \
            --no-install-project
}

function set_device_type() {
    if [ ! -f /proc/device-tree/model ] && [ "$(uname -m)" = "x86_64" ]; then
        export DEVICE_TYPE="x86"
    elif grep -qF "Raspberry Pi 5" /proc/device-tree/model || grep -qF "Compute Module 5" /proc/device-tree/model; then
        export DEVICE_TYPE="pi5"
    elif grep -qF "Raspberry Pi 4" /proc/device-tree/model || grep -qF "Compute Module 4" /proc/device-tree/model; then
        export DEVICE_TYPE="pi4"
    elif grep -qF "Raspberry Pi 3" /proc/device-tree/model || grep -qF "Compute Module 3" /proc/device-tree/model; then
        export DEVICE_TYPE="pi3"
    elif grep -qF "Raspberry Pi 2" /proc/device-tree/model; then
        export DEVICE_TYPE="pi2"
    else
        export DEVICE_TYPE="pi1"
    fi
}

function run_ansible_playbook() {
    display_section "Run the Anthias Ansible Playbook"
    set_device_type

    # Forwarded to the playbook so the screenly role can pin
    # /usr/local/sbin/upgrade_anthias.sh to the same ref the user picked.
    export ANTHIAS_BRANCH="${BRANCH}"

    cd "${ANTHIAS_REPO_DIR}/ansible"

    # If the user doesn't have NOPASSWD sudo yet (first install), Ansible
    # needs --ask-become-pass to elevate. The blanket NOPASSWD rule is
    # written by modify_permissions later in this script.
    if [ ! -f "/etc/sudoers.d/010_${USER}-nopasswd" ]; then
        ANSIBLE_PLAYBOOK_ARGS+=("--ask-become-pass")
        gum format \
            "**Note:** Ansible may prompt for your sudo password below."
        echo
    fi

    if [ "$ARCHITECTURE" == "x86_64" ]; then
        ANSIBLE_PLAYBOOK_ARGS+=("--skip-tags" "raspberry-pi")
    fi

    # Point Ansible at the venv's Python — we no longer install
    # python3 system-wide in the bootstrap step.
    export ANSIBLE_PYTHON_INTERPRETER="/home/${USER}/installer_venv/bin/python"

    sudo -E -u "${USER}" \
        "/home/${USER}/installer_venv/bin/ansible-playbook" \
        site.yml "${ANSIBLE_PLAYBOOK_ARGS[@]}"
}

function upgrade_docker_containers() {
    display_section "Initialize/Upgrade Docker Containers"

    # Pull upgrade_containers.sh from the same ref the user picked,
    # not master, so a tagged install gets the matching upgrade script.
    wget -q \
        "${GITHUB_RAW_URL}/${BRANCH}/bin/upgrade_containers.sh" \
        -O "${UPGRADE_SCRIPT_PATH}"

    sudo -u "${USER}" \
        DOCKER_TAG="${DOCKER_TAG}" \
        GIT_BRANCH="${BRANCH}" \
        "${UPGRADE_SCRIPT_PATH}"
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
    sudo chown -R "${USER}:${USER}" "/home/${USER}"

    # Run `sudo` without entering a password.
    local SUDOERS_FILE="/etc/sudoers.d/010_${USER}-nopasswd"
    if [ ! -f "${SUDOERS_FILE}" ]; then
        echo "${USER} ALL=(ALL) NOPASSWD: ALL" | \
            sudo tee "${SUDOERS_FILE}" > /dev/null
        sudo chmod 0440 "${SUDOERS_FILE}"
    fi
}

function write_anthias_version() {
    local GIT_BRANCH GIT_SHORT_HASH ANTHIAS_VERSION
    GIT_BRANCH=$(git -C "${ANTHIAS_REPO_DIR}" rev-parse --abbrev-ref HEAD)
    GIT_SHORT_HASH=$(git -C "${ANTHIAS_REPO_DIR}" rev-parse --short HEAD)
    ANTHIAS_VERSION="Anthias Version: ${GIT_BRANCH}@${GIT_SHORT_HASH}"

    {
        echo "${ANTHIAS_VERSION}"
        lsb_release -a 2> /dev/null
    } > ~/version.md
}

function post_installation() {
    display_section "Installation Complete"

    echo

    gum style --foreground "#00FFFF" \
        "A reboot is required to complete the installation." \
        | gum format

    if [ -n "${SSH_CONNECTION:-}" ]; then
        echo
        gum style --foreground "#FFAA00" \
            "**Heads up:** you appear to be connected over SSH; rebooting will drop your session." \
            | gum format
    fi

    echo

    gum confirm "Do you want to reboot now?" && \
        gum style --foreground "#FF00FF" "Rebooting..." | gum format && \
        sudo reboot
}

function set_custom_version() {
    BRANCH=$(
        gum input \
            --header "Enter the tag name you want to install" \
    )

    local STATUS_CODE
    STATUS_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        "${GITHUB_API_REPO_URL}/git/refs/tags/${BRANCH}")

    if [ "$STATUS_CODE" -ne 200 ]; then
        gum style "Invalid tag name." \
            | gum format
        echo
        exit 1
    fi

    local DOCKER_TAG_FILE_URL="${GITHUB_RELEASES_URL}/download/${BRANCH}/docker-tag"
    STATUS_CODE=$(curl -sL -o /dev/null -w "%{http_code}" \
        "$DOCKER_TAG_FILE_URL")

    if [ "$STATUS_CODE" -ne 200 ]; then
        gum style "This version doesn't have a \`docker-tag\` file." \
            | gum format
        echo
        exit 1
    fi

    DOCKER_TAG=$(curl -sL "$DOCKER_TAG_FILE_URL")
}

function main() {
    install_prerequisites && clear

    display_banner "${TITLE_TEXT}"

    gum format "${INTRO_MESSAGE[@]}"
    echo
    gum confirm "Do you still want to continue?" || exit 0

    if gum confirm "${MANAGE_NETWORK_PROMPT[@]}"; then
        export MANAGE_NETWORK="Yes"
    else
        export MANAGE_NETWORK="No"
    fi

    VERSION=$(
        gum choose \
            --header "${VERSION_PROMPT[*]}" \
            -- "${VERSION_PROMPT_CHOICES[@]}"
    )

    if [ "${VERSION}" == "latest" ]; then
        BRANCH="master"
    else
        set_custom_version
    fi

    if gum confirm "${SYSTEM_UPGRADE_PROMPT[@]}"; then
        SYSTEM_UPGRADE="Yes"
    else
        SYSTEM_UPGRADE="No"
        ANSIBLE_PLAYBOOK_ARGS+=("--skip-tags" "system-upgrade")
    fi

    display_section "User Input Summary"
    gum format "**Manage Network:**     ${MANAGE_NETWORK}"
    gum format "**Branch/Tag:**         \`${BRANCH}\`"
    gum format "**System Upgrade:**     ${SYSTEM_UPGRADE}"
    gum format "**Docker Tag Prefix:**  \`${DOCKER_TAG}\`"

    initialize_ansible
    initialize_locales
    install_packages
    clone_repo
    install_ansible
    run_ansible_playbook

    upgrade_docker_containers
    cleanup
    modify_permissions

    write_anthias_version
    post_installation
}

main
