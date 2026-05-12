#!/bin/bash

set -euo pipefail

BRANCH="master"
ANSIBLE_PLAYBOOK_ARGS=()
REPOSITORY="https://github.com/Screenly/Anthias.git"
ANTHIAS_REPO_DIR="/home/${USER}/anthias"
GITHUB_API_REPO_URL="https://api.github.com/repos/Screenly/Anthias"
GITHUB_RELEASES_URL="https://github.com/Screenly/Anthias/releases"
GITHUB_RAW_URL="https://raw.githubusercontent.com/Screenly/Anthias"
DOCKER_TAG="latest"
UPGRADE_SCRIPT_PATH="${ANTHIAS_REPO_DIR}/bin/upgrade_containers.sh"
ARCHITECTURE=$(uname -m)

# Pin uv to match docker/uv-builder.j2 so host and image use the same binary.
UV_PIN_VERSION="0.9.17"

# Ephemeral install-time venv for ansible-core + the host-group deps.
# Created in install_ansible(), torn down by the EXIT trap below — the
# venv has no runtime role, so there's no reason to leave 100+ MB of
# Python state in $HOME after the playbook finishes. Earlier releases
# kept it at /home/$USER/installer_venv, which deserved a heuristic to
# detect and replace pre-uv layouts; with the venv ephemeral, that's no
# longer needed.
INSTALLER_VENV=""

cleanup_installer_venv() {
    if [ -n "${INSTALLER_VENV}" ] && [ -d "${INSTALLER_VENV}" ]; then
        # Ansible's `become: true` tasks run python from this venv as
        # root and end up writing __pycache__/*.pyc entries owned by
        # root. A plain user-level rm therefore can't clean the tree.
        # By the time the EXIT trap fires, modify_permissions() has
        # already installed the NOPASSWD sudoers rule for the install
        # user, so `sudo -n` succeeds non-interactively. Fall back to
        # a best-effort user-space rm if the trap fires earlier in
        # the script (i.e. before modify_permissions ran).
        sudo -n rm -rf "${INSTALLER_VENV}" 2>/dev/null \
            || rm -rf "${INSTALLER_VENV}" 2>/dev/null \
            || true
    fi
}
trap cleanup_installer_venv EXIT

INTRO_MESSAGE=(
    "Anthias runs on a dedicated Raspberry Pi (2/3/4-64-bit/5) or x86 device."
    "The host will be repurposed for digital signage — on a Pi you lose the"
    "regular desktop environment, and on x86 the machine should not be used"
    "for anything else."
    ""
    "When prompted for the version, you can choose between the following:"
    "  - latest: Installs the latest version from the master branch."
    "  - tag: Installs a pinned version based on the tag name."
    ""
    "Take note that 'latest' is a rolling release."
)
MANAGE_NETWORK_PROMPT=(
    "Would you like Anthias to manage the network for you?"
)
VERSION_PROMPT=(
    "Which version of Anthias would you like to install?"
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

# Anthias brand styling. The CSS palette (sass/_variables.scss) is built
# from purples (#270035–#8819C7) and yellows (#FFD800–#FFF963); whiptail's
# NEWT_COLORS only accepts the 16 named ANSI colors, so we map purple to
# magenta/brightmagenta and the accent to yellow. The ANSI escapes below
# are used for the plain-text banner/section headers that print between
# long-running install steps, kept off when stdout is not a TTY.
export NEWT_COLORS='
root=,magenta
border=brightmagenta,white
window=black,white
shadow=black,gray
title=white,magenta
button=black,yellow
actbutton=white,brightmagenta
compactbutton=black,white
checkbox=black,white
actcheckbox=yellow,brightmagenta
entry=black,white
disentry=gray,white
label=black,white
listbox=black,white
actlistbox=white,brightmagenta
sellistbox=black,yellow
actsellistbox=white,brightmagenta
textbox=black,white
acttextbox=white,brightmagenta
emptyscale=,white
fullscale=,brightmagenta
helpline=yellow,magenta
roottext=yellow,magenta
'

if [ -t 1 ]; then
    ANSI_PURPLE=$'\033[1;35m'
    ANSI_YELLOW=$'\033[1;33m'
    ANSI_RESET=$'\033[0m'
else
    ANSI_PURPLE=''
    ANSI_YELLOW=''
    ANSI_RESET=''
fi

# whiptail ships with Debian/Raspbian by default; the apt install is a
# safety net for minimal images. jq is consumed elsewhere in the install
# pipeline.
function install_prerequisites() {
    if [ -f /usr/bin/whiptail ] && [ -f /usr/bin/jq ]; then
        return
    fi

    sudo apt -y update && sudo apt -y install whiptail jq
}

function display_banner() {
    local TITLE="${1:-Anthias Installer}"
    echo
    echo "${ANSI_PURPLE}${TITLE}${ANSI_RESET}"
    echo
}

function display_section() {
    local TITLE="${1:-Section}"
    local LINE="======================================================================"
    echo
    echo "${ANSI_YELLOW}${LINE}${ANSI_RESET}"
    echo "${ANSI_PURPLE}  ${TITLE}${ANSI_RESET}"
    echo "${ANSI_YELLOW}${LINE}${ANSI_RESET}"
    echo
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
        "gettext-base"
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

function migrate_repo_dir() {
    # Rename ~/screenly -> ~/anthias before clone_repo runs so the user's
    # existing checkout state, hooks, and any local changes are preserved
    # rather than starting fresh in a new directory. Config and asset
    # dirs are migrated separately by bin/migrate_legacy_paths.sh inside
    # the cloned repo (see post_clone_migrate_legacy_paths).
    local OLD_REPO_DIR="/home/${USER}/screenly"
    if [ -d "${OLD_REPO_DIR}/.git" ] && [ ! -e "${ANTHIAS_REPO_DIR}" ]; then
        display_section "Rename ${OLD_REPO_DIR} -> ${ANTHIAS_REPO_DIR}"
        mv "${OLD_REPO_DIR}" "${ANTHIAS_REPO_DIR}"
        # Back-compat symlink for one release.
        ln -s "${ANTHIAS_REPO_DIR}" "${OLD_REPO_DIR}"
    fi
}

function post_clone_migrate_legacy_paths() {
    # Run the in-repo migration helper once the repo is on disk. Handles
    # ~/.screenly -> ~/.anthias, ~/screenly_assets -> ~/anthias_assets,
    # the screenly.{db,conf} -> anthias.{db,conf} renames, and the
    # back-compat symlinks. Idempotent / no-op on fresh installs.
    display_section "Migrate Legacy 'screenly' Data Paths"
    "${ANTHIAS_REPO_DIR}/bin/migrate_legacy_paths.sh"
}

function clone_repo() {
    display_section "Clone Anthias Repository"

    if [ ! -d "${ANTHIAS_REPO_DIR}/.git" ]; then
        git clone "${REPOSITORY}" "${ANTHIAS_REPO_DIR}"
    fi
    git -C "${ANTHIAS_REPO_DIR}" fetch --tags origin
    git -C "${ANTHIAS_REPO_DIR}" checkout "${BRANCH}"

    # Releases are pinned via tags (e.g. v0.20.5), which fetch into
    # refs/tags/ — `origin/v0.20.5` is not a valid ref. Resolve tags
    # explicitly via refs/tags/${BRANCH} and fall through to
    # origin/${BRANCH} only for actual branches.
    local RESET_REF
    if git -C "${ANTHIAS_REPO_DIR}" show-ref --verify --quiet \
        "refs/tags/${BRANCH}"; then
        RESET_REF="refs/tags/${BRANCH}"
    elif git -C "${ANTHIAS_REPO_DIR}" show-ref --verify --quiet \
        "refs/remotes/origin/${BRANCH}"; then
        RESET_REF="origin/${BRANCH}"
    else
        echo "error: '${BRANCH}' is neither a tag nor a remote branch" >&2
        exit 1
    fi
    git -C "${ANTHIAS_REPO_DIR}" reset --hard "${RESET_REF}"
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

    # Provision the venv in a fresh tmpdir each run so a previous
    # install's interpreter/layout can never collide with this one.
    # Earlier releases pinned a constraint here (`--python ">=3.13"`)
    # which uv resolves to the latest matching managed interpreter — on
    # Bookworm that picked 3.14, while pyproject's `.python-version`
    # asks for 3.13, and the disagreement triggered a tear-down on
    # every subsequent `uv sync` (Fixes #2842). Letting `.python-version`
    # be the single source of truth keeps both invocations aligned.
    INSTALLER_VENV=$(mktemp -d -t anthias-installer-venv.XXXXXX)
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
        export DEVICE_TYPE="pi4-64"
    elif grep -qF "Raspberry Pi 3" /proc/device-tree/model || grep -qF "Compute Module 3" /proc/device-tree/model; then
        export DEVICE_TYPE="pi3"
    elif grep -qF "Raspberry Pi 2" /proc/device-tree/model; then
        export DEVICE_TYPE="pi2"
    else
        echo "Unsupported Raspberry Pi model. Anthias supports Pi 2/3/4 (64-bit)/5 and x86." >&2
        exit 1
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
        echo "Note: Ansible may prompt for your sudo password below."
        echo
    fi

    if [ "$ARCHITECTURE" == "x86_64" ]; then
        ANSIBLE_PLAYBOOK_ARGS+=("--skip-tags" "raspberry-pi")
    fi

    # Point Ansible at the venv's Python — we no longer install
    # python3 system-wide in the bootstrap step. Both this and the
    # ansible-playbook path interpolate INSTALLER_VENV at parse time, so
    # the literal tmpdir path crosses the sudo boundary as the argv —
    # no env-forwarding gymnastics needed beyond -E preserving
    # ANSIBLE_PYTHON_INTERPRETER itself.
    export ANSIBLE_PYTHON_INTERPRETER="${INSTALLER_VENV}/bin/python"

    sudo -E -u "${USER}" \
        "${INSTALLER_VENV}/bin/ansible-playbook" \
        site.yml "${ANSIBLE_PLAYBOOK_ARGS[@]}"
}

function upgrade_docker_containers() {
    display_section "Initialize/Upgrade Docker Containers"

    # Pull upgrade_containers.sh from the same ref the user picked,
    # not master, so a tagged install gets the matching upgrade script.
    curl -fsSL \
        "${GITHUB_RAW_URL}/${BRANCH}/bin/upgrade_containers.sh" \
        -o "${UPGRADE_SCRIPT_PATH}"

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

    local PROMPT="A reboot is required to complete the installation."
    if [ -n "${SSH_CONNECTION:-}" ]; then
        PROMPT+=$'\n\nHeads up: you appear to be connected over SSH; rebooting will drop your session.'
    fi
    PROMPT+=$'\n\nDo you want to reboot now?'

    if whiptail \
        --title "Anthias Installer" \
        --yesno "${PROMPT}" 14 70; then
        echo "Rebooting..."
        sudo reboot
    fi
}

function set_custom_version() {
    BRANCH=$(
        whiptail \
            --title "Anthias Installer" \
            --inputbox "Enter the tag name you want to install:" \
            10 70 \
            3>&1 1>&2 2>&3
    )

    local STATUS_CODE
    STATUS_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        "${GITHUB_API_REPO_URL}/git/refs/tags/${BRANCH}")

    if [ "$STATUS_CODE" -ne 200 ]; then
        whiptail \
            --title "Anthias Installer" \
            --msgbox "Invalid tag name." 8 60
        exit 1
    fi

    local DOCKER_TAG_FILE_URL="${GITHUB_RELEASES_URL}/download/${BRANCH}/docker-tag"
    STATUS_CODE=$(curl -sL -o /dev/null -w "%{http_code}" \
        "$DOCKER_TAG_FILE_URL")

    if [ "$STATUS_CODE" -ne 200 ]; then
        whiptail \
            --title "Anthias Installer" \
            --msgbox "This version doesn't have a docker-tag file." 8 60
        exit 1
    fi

    DOCKER_TAG=$(curl -sL "$DOCKER_TAG_FILE_URL")
}

function main() {
    install_prerequisites && clear

    display_banner "${TITLE_TEXT}"

    local INTRO
    INTRO=$(printf '%s\n' "${INTRO_MESSAGE[@]}")
    whiptail \
        --title "Anthias Installer" \
        --yesno "${INTRO}"$'\n\nDo you still want to continue?' \
        20 76 \
        || exit 0

    if whiptail \
        --title "Anthias Installer" \
        --yesno "${MANAGE_NETWORK_PROMPT[*]}" 10 70; then
        export MANAGE_NETWORK="Yes"
    else
        export MANAGE_NETWORK="No"
    fi

    VERSION=$(
        whiptail \
            --title "Anthias Installer" \
            --menu "${VERSION_PROMPT[*]}" 15 70 4 \
            "latest" "Latest version from the master branch (rolling)" \
            "tag"    "Pinned version selected by tag name" \
            3>&1 1>&2 2>&3
    )

    if [ "${VERSION}" == "latest" ]; then
        BRANCH="master"
    else
        set_custom_version
    fi

    if whiptail \
        --title "Anthias Installer" \
        --yesno "${SYSTEM_UPGRADE_PROMPT[*]}" 10 70; then
        SYSTEM_UPGRADE="Yes"
    else
        SYSTEM_UPGRADE="No"
        ANSIBLE_PLAYBOOK_ARGS+=("--skip-tags" "system-upgrade")
    fi

    display_section "User Input Summary"
    echo "Manage Network:     ${MANAGE_NETWORK}"
    echo "Branch/Tag:         ${BRANCH}"
    echo "System Upgrade:     ${SYSTEM_UPGRADE}"
    echo "Docker Tag Prefix:  ${DOCKER_TAG}"

    initialize_ansible
    initialize_locales
    install_packages
    migrate_repo_dir
    clone_repo
    post_clone_migrate_legacy_paths
    install_ansible
    run_ansible_playbook

    upgrade_docker_containers
    cleanup
    modify_permissions

    write_anthias_version
    post_installation
}

main
