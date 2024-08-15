#!/bin/bash -e

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

WEB_UPGRADE=false
BRANCH_VERSION=
MANAGE_NETWORK=
UPGRADE_SYSTEM=

if [ -f .env ]; then
    source .env
fi

while getopts ":w:b:n:s:" arg; do
    case "${arg}" in
        w)
            WEB_UPGRADE=true
        ;;
        b)
            BRANCH_VERSION=${OPTARG}
        ;;
        n)
            MANAGE_NETWORK=${OPTARG}
        ;;
        s)
            UPGRADE_SYSTEM=${OPTARG}
        ;;
    esac
done

if [ "$WEB_UPGRADE" = false ]; then

  # Make sure the command is launched interactive.
  if ! [ -t 0  ]; then
    echo -e "Detected old installation command. Please use:\n$ bash <(curl -sL https://install-anthias.srly.io)"
    exit 1
  fi

  # clear screen
  clear;

  # Set color of logo
  tput setaf 6
  tput bold

  cat << EOF

       d8888            888     888
      d88888            888     888       888
     d88P888            888     888
    d88P 888  88888b.   888888  88888b.   888   8888b.   .d8888b
   d88P  888  888 '88b  888     888 '88b  888      '88b  88K
  d88P   888  888  888  888     888  888  888  .d888888  'Y8888b.
 d8888888888  888  888  Y88b.   888  888  888  888  888       X88
d88P     888  888  888   Y888   888  888  888  'Y888888   88888P'
==================================================================


EOF

# Reset color
tput sgr 0

echo -e "Anthias requires a dedicated Raspberry Pi / SD card.\nYou will not be able to use the regular desktop environment once installed.\n"
read -p "Do you still want to continue? (y/N)" -n 1 -r -s INSTALL
if [ "$INSTALL" != 'y' ]; then
    echo
    exit 1
fi

export DOCKER_TAG="latest"
export BRANCH="master"

  echo && read -p "Do you want Anthias to manage your network? This is recommended for most users because it adds features to manage your network. (Y/n)" -n 1 -r -s NETWORK && echo
  echo && read -p "Would you like to install the experimental version of Anthias instead? (y/N)" -n 1 -r -s IS_EXPERIMENTAL && echo
  echo && read -p "Would you like to perform a full system upgrade? (y/N)" -n 1 -r -s UPGRADE && echo

  echo "Which version of Anthias would you like to install?"
  echo " Press (1) for the latest version."
  echo " Press (2) for the experimental version."
  echo " Press (3) for a specific version (e.g. v0.0.1)."
  echo -n "Choice: "

  read -n 1 -r -s VERSION_SELECTION
    case $VERSION_SELECTION in
        1)
            export BRANCH="master"
            ;;
        2)
            export BRANCH="experimental"
            ;;
        3)
            echo "Please enter the version you would like to install (e.g. v0.0.1):"
            read -r VERSION
            STATUS_CODE=$(curl -s -o /dev/null -w "%{http_code}\n" https://api.github.com/repos/Screenly/Anthias/releases/tags/${VERSION})
            if [ $STATUS_CODE -ne 200 ]; then
                echo "(Error) The version you entered does not exist, installer will now exit.";
                exit 1
            else
                export BRANCH=$VERSION
                export DOCKER_TAG_PREFIX="$(curl -sL https://github.com/Screenly/Anthias/releases/download/${VERSION}/docker-tag)"
            fi
            ;;
        *)
            echo "(Error) That was not an option, installer will now exit.";
            exit 1
            ;;
    esac

  if [ "$UPGRADE" != 'y' ]; then
      EXTRA_ARGS=("--skip-tags" "system-upgrade")
  fi

elif [ "$WEB_UPGRADE" = true ]; then
  if [ -z "${BRANCH}" ]; then
    if [ "$BRANCH_VERSION" = "latest" ]; then
      export DOCKER_TAG="latest"
      BRANCH="master"
    elif [ "$BRANCH_VERSION" = "production" ]; then
      export DOCKER_TAG="production"
      BRANCH="production"
    else
      echo -e "Invalid -b parameter."
      exit 1
    fi
  fi
  if [ "$MANAGE_NETWORK" = false ]; then
    NETWORK="n"
  elif [ "$MANAGE_NETWORK" = true ]; then
    NETWORK="y"
  else
    echo -e "Invalid -n parameter."
    exit 1
  fi
  if [ "$UPGRADE_SYSTEM" = false ]; then
      EXTRA_ARGS=("--skip-tags" "system-upgrade")
  else
    echo -e "Invalid -s parameter."
    exit 1
  fi
else
  echo -e "Invalid -w parameter."
  exit 1
fi

if [ -z "${REPOSITORY}" ]; then
  if [ "$WEB_UPGRADE" = false ]; then
    set -x
    REPOSITORY=${1:-https://github.com/screenly/anthias.git}
  else
    set -e
    REPOSITORY=https://github.com/screenly/anthias.git
  fi
fi

if [ ! -d /home/${USER}/screenly ]; then
    mkdir /home/${USER}/screenly
fi

sudo mkdir -p /etc/ansible
echo -e "[local]\nlocalhost ansible_connection=local" | sudo tee /etc/ansible/hosts > /dev/null

if [ ! -f /etc/locale.gen ]; then
    # No locales found. Creating locales with default UK/US setup.
    echo -e "en_GB.UTF-8 UTF-8\nen_US.UTF-8 UTF-8" | sudo tee /etc/locale.gen > /dev/null
    sudo locale-gen
fi

RASPBIAN_VERSION=$(lsb_release -rs)
APT_INSTALL_ARGS=(
  "git"
  "libffi-dev"
  "libssl-dev"
  "whois"
)

if [ "$RASPBIAN_VERSION" = "12" ]; then
  APT_INSTALL_ARGS+=("python3-full")
else
  APT_INSTALL_ARGS+=("python3" "python3-dev" "python3-pip" "python3-venv")
fi

sudo sed -i 's/apt.screenlyapp.com/archive.raspbian.org/g' /etc/apt/sources.list
sudo apt update -y
sudo apt-get install -y --no-install-recommends "${APT_INSTALL_ARGS[@]}"

if [ "$NETWORK" == 'y' ]; then
  export MANAGE_NETWORK=true
  sudo apt-get install -y network-manager
else
  export MANAGE_NETWORK=false
fi

# Install Ansible from requirements file.
ANSIBLE_VERSION=$(curl -s https://raw.githubusercontent.com/screenly/anthias/$BRANCH/requirements/requirements.host.txt | grep ansible)

SUDO_ARGS=()

if python3 -c "import venv" &> /dev/null; then
  echo "Module venv is detected. Activating virtual environment..."

  python3 -m venv /home/${USER}/installer_venv
  source /home/${USER}/installer_venv/bin/activate

  SUDO_ARGS+=("--preserve-env" "env" "PATH=$PATH")
fi

# @TODO
# Remove me later. Cryptography 38.0.3 won't build at the moment.
# See https://github.com/screenly/anthias/issues/1654
sudo ${SUDO_ARGS[@]} pip install cryptography==38.0.2
sudo ${SUDO_ARGS[@]} pip install "$ANSIBLE_VERSION"

sudo -u ${USER} ${SUDO_ARGS[@]} ansible localhost \
    -m git \
    -a "repo=$REPOSITORY dest=/home/${USER}/screenly version=$BRANCH force=no"
cd /home/${USER}/screenly/ansible

sudo -E -u ${USER} ${SUDO_ARGS[@]} ansible-playbook site.yml "${EXTRA_ARGS[@]}"

# Pull down and install containers
sudo -u ${USER} DOCKER_TAG=${DOCKER_TAG_PREFIX} /home/${USER}/screenly/bin/upgrade_containers.sh

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

sudo chown -R ${USER}:${USER} /home/${USER}

# Run sudo w/out password
if [ ! -f /etc/sudoers.d/010_${USER}-nopasswd ]; then
  echo "${USER} ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/010_${USER}-nopasswd > /dev/null
  sudo chmod 0440 /etc/sudoers.d/010_${USER}-nopasswd
fi

echo -e "Anthias version: $(git rev-parse --abbrev-ref HEAD)@$(git rev-parse --short HEAD)\n$(lsb_release -a)" > ~/version.md

if [ "$WEB_UPGRADE" = false ]; then
  set +x
else
  set +e
fi

echo "Installation completed."

if [ "$WEB_UPGRADE" = false ]; then
  POST_INSTALL_MESSAGE=""

  if [ -f /var/run/reboot-required ]; then
    POST_INSTALL_MESSAGE="Please reboot and run /home/$USER/screenly/bin/upgrade_containers.sh to complete the installation"
  else
    POST_INSTALL_MESSAGE="You need to reboot the system for the installation to complete"
  fi

  read -p "${POST_INSTALL_MESSAGE}. Would you like to reboot now? (y/N)" -n 1 -r -s REBOOT && echo
  if [ "$REBOOT" == 'y' ]; then
    sudo reboot
  fi
fi
