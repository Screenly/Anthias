#!/bin/bash -e

WEB_UPGRADE=false
BRANCH_VERSION=
MANAGE_NETWORK=
UPGRADE_SYSTEM=

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
    echo -e "Detected old installation command. Please use:\n$ bash <(curl -sL https://www.screenlyapp.com/install-ose.sh)"
    exit 1
  fi

  # Set color of logo
  tput setaf 4

  cat << EOF
       _____                           __         ____  _____ ______
      / ___/_____________  ___  ____  / /_  __   / __ \/ ___// ____/
      \__ \/ ___/ ___/ _ \/ _ \/ __ \/ / / / /  / / / /\__ \/ __/
     ___/ / /__/ /  /  __/  __/ / / / / /_/ /  / /_/ /___/ / /___
    /____/\___/_/   \___/\___/_/ /_/_/\__, /   \____//____/_____/
                                     /____/
EOF

  # Reset color
  tput sgr 0

  echo -e "Screenly OSE requires a dedicated Raspberry Pi / SD card.\nYou will not be able to use the regular desktop environment once installed.\n"
  read -p "Do you still want to continue? (y/N)" -n 1 -r -s INSTALL
  if [ "$INSTALL" != 'y' ]; then
    echo
    exit 1
  fi

  echo && read -p "Would you like to use the experimental branch? It contains the last major changes, such as the new browser and migrating to Docker (y/N)" -n 1 -r -s EXP && echo
  if [ "$EXP" != 'y'  ]; then
    echo && read -p "Would you like to use the development (master) branch? You will get the latest features, but things may break. (y/N)" -n 1 -r -s DEV && echo
    if [ "$DEV" != 'y'  ]; then
      export DOCKER_TAG="production"
      BRANCH="production"
    else
      export DOCKER_TAG="latest"
      BRANCH="master"
    fi
  else
    export DOCKER_TAG="experimental"
    BRANCH="experimental"
  fi

  echo && read -p "Would you like to install the WoTT agent to help you manage security of your Raspberry Pi? (y/N)" -n 1 -r -s WOTT && echo
  if [ "$WOTT" = 'y' ]; then
      curl -s https://packagecloud.io/install/repositories/wott/agent/script.deb.sh | sudo bash
      sudo apt install wott-agent
  fi

  echo && read -p "Do you want Screenly to manage your network? This is recommended for most users because this adds features to manage your network. (Y/n)" -n 1 -r -s NETWORK && echo

  echo && read -p "Would you like to perform a full system upgrade as well? (y/N)" -n 1 -r -s UPGRADE && echo
  if [ "$UPGRADE" != 'y' ]; then
    EXTRA_ARGS="--skip-tags enable-ssl,system-upgrade"
  else
    EXTRA_ARGS="--skip-tags enable-ssl"
  fi

elif [ "$WEB_UPGRADE" = true ]; then

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

  if [ "$MANAGE_NETWORK" = false ]; then
    NETWORK="y"
  elif [ "$MANAGE_NETWORK" = true ]; then
    NETWORK="n"
  else
    echo -e "Invalid -n parameter."
    exit 1
  fi

  if [ "$UPGRADE_SYSTEM" = false ]; then
    EXTRA_ARGS="--skip-tags enable-ssl,system-upgrade"
  elif [ "$UPGRADE_SYSTEM" = true ]; then
    EXTRA_ARGS="--skip-tags enable-ssl"
  else
    echo -e "Invalid -s parameter."
    exit 1
  fi

else
  echo -e "Invalid -w parameter."
  exit 1
fi

if grep -qF "Raspberry Pi 3" /proc/device-tree/model; then
  export DEVICE_TYPE="pi3"
elif grep -qF "Raspberry Pi 2" /proc/device-tree/model; then
  export DEVICE_TYPE="pi2"
else
  export DEVICE_TYPE="pi1"
fi

if [ "$WEB_UPGRADE" = false ]; then
  set -x
  REPOSITORY=${1:-https://github.com/screenly/screenly-ose.git}
else
  set -e
  REPOSITORY=https://github.com/screenly/screenly-ose.git
fi

sudo mkdir -p /etc/ansible
echo -e "[local]\nlocalhost ansible_connection=local" | sudo tee /etc/ansible/hosts > /dev/null

if [ ! -f /etc/locale.gen ]; then
  # No locales found. Creating locales with default UK/US setup.
  echo -e "en_GB.UTF-8 UTF-8\nen_US.UTF-8 UTF-8" | sudo tee /etc/locale.gen > /dev/null
  sudo locale-gen
fi

sudo sed -i 's/apt.screenlyapp.com/archive.raspbian.org/g' /etc/apt/sources.list
sudo apt update -y
sudo apt-get purge -y python-setuptools python-pip python-pyasn1
sudo apt-get install -y python-dev git-core libffi-dev libssl-dev
curl -s https://bootstrap.pypa.io/get-pip.py | sudo python

if [ "$NETWORK" == 'y' ]; then
  export MANAGE_NETWORK=true
  sudo apt-get install -y network-manager
else
  export MANAGE_NETWORK=false
fi

sudo pip install ansible==2.8.2

sudo -u pi ansible localhost -m git -a "repo=$REPOSITORY dest=/home/pi/screenly version=$BRANCH"
cd /home/pi/screenly/ansible

sudo -E ansible-playbook site.yml $EXTRA_ARGS

sudo apt-get autoclean
sudo apt-get clean
sudo find /usr/share/doc -depth -type f ! -name copyright -delete
sudo find /usr/share/doc -empty -delete
sudo rm -rf /usr/share/man /usr/share/groff /usr/share/info /usr/share/lintian /usr/share/linda /var/cache/man
sudo find /usr/share/locale -type f ! -name 'en' ! -name 'de*' ! -name 'es*' ! -name 'ja*' ! -name 'fr*' ! -name 'zh*' -delete
sudo find /usr/share/locale -mindepth 1 -maxdepth 1 ! -name 'en*' ! -name 'de*' ! -name 'es*' ! -name 'ja*' ! -name 'fr*' ! -name 'zh*' -exec rm -r {} \;

cd /home/pi/screenly && git rev-parse HEAD > /home/pi/.screenly/latest_screenly_sha
sudo chown -R pi:pi /home/pi

# Need a password for commands with sudo
if [ "$BRANCH" = "master" ] || [ "$BRANCH" = "production" ]; then
  sudo rm -f /etc/sudoers.d/010_pi-nopasswd
else
  # Temporarily necessary because web upgrade only for the master branch
  echo "pi ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/010_pi-nopasswd > /dev/null
  sudo chmod 0440 /etc/sudoers.d/010_pi-nopasswd
fi

# Setup a new pi password
if [ "$BRANCH" = "master" ] || [ "$BRANCH" = "production" ] && [ "$WEB_UPGRADE" = false ]; then
  set +e
  passwd
  set -e
fi

echo -e "Screenly version: $(git rev-parse --abbrev-ref HEAD)@$(git rev-parse --short HEAD)\n$(lsb_release -a)" > ~/version.md

if [ "$WEB_UPGRADE" = false ]; then
  set +x
else
  set +e
fi

echo "Installation completed."

if [ "$WEB_UPGRADE" = false ]; then
  read -p "You need to reboot the system for the installation to complete. Would you like to reboot now? (y/N)" -n 1 -r -s REBOOT && echo
  if [ "$REBOOT" == 'y' ]; then
    sudo reboot
  fi
fi
