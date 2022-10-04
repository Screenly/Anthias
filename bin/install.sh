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
    echo -e "Detected old installation command. Please use:\n$ bash <(curl -sL https://www.screenlyapp.com/install-ose.sh)"
    exit 1
  fi

  # clear screen
  clear;

  # Set color of logo
  tput setaf 6
  tput bold

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

  if [ "$USER" != 'pi' ]; then
      echo "Screenly OSE must be installed as the user 'pi' (with sudo permission)."
      exit 1
  fi

  echo -e "Screenly OSE requires a dedicated Raspberry Pi / SD card.\nYou will not be able to use the regular desktop environment once installed.\n"
  read -p "Do you still want to continue? (y/N)" -n 1 -r -s INSTALL
  if [ "$INSTALL" != 'y' ]; then
    echo
    exit 1
  fi


# @TODO Re-enable the 'production' branch once we've merged master into production
#echo -e "\n________________________________________\n"
#echo -e "Which version/branch of Screenly OSE would you like to install:\n"
#echo " Press (1) for the Production branch, which is the latest stable."
#echo " Press (2) for the Development/Master branch, which has the latest features and fixes, but things may break."
#echo ""

#read -n 1 -r -s BRANCHSELECTION
#case $BRANCHSELECTION in
#  1) echo "You selected: Production";export DOCKER_TAG="production";BRANCH="production"
#    ;;
#  2) echo "You selected: Development/Master";export DOCKER_TAG="latest";BRANCH="master"
#    ;;
#  *) echo "(Error) That was not an option, installer will now exit.";exit
#    ;;
#esac

# Remove these once the above code has been restored.
export DOCKER_TAG="latest"
export BRANCH="master"

  echo && read -p "Do you want Screenly OSE to manage your network? This is recommended for most users because this adds features to manage your network. (Y/n)" -n 1 -r -s NETWORK && echo

  echo && read -p "Would you like to perform a full system upgrade as well? (y/N)" -n 1 -r -s UPGRADE && echo
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
    REPOSITORY=${1:-https://github.com/screenly/screenly-ose.git}
  else
    set -e
    REPOSITORY=https://github.com/screenly/screenly-ose.git
  fi
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
sudo apt-get install -y  --no-install-recommends \
    git \
    libffi-dev \
    libssl-dev \
    python3 \
    python3-dev \
    python3-pip \
    whois

if [ "$NETWORK" == 'y' ]; then
  export MANAGE_NETWORK=true
  sudo apt-get install -y network-manager
else
  export MANAGE_NETWORK=false
fi

# Install Ansible from requirements file.
if [ "$BRANCH" = "master" ]; then
    ANSIBLE_VERSION=$(curl -s https://raw.githubusercontent.com/Screenly/screenly-ose/$BRANCH/requirements/requirements.host.txt | grep ansible)
else
    ANSIBLE_VERSION=ansible==2.8.8
fi

sudo pip install "$ANSIBLE_VERSION"

sudo -u pi ansible localhost \
    -m git \
    -a "repo=$REPOSITORY dest=/home/pi/screenly version=$BRANCH force=no"
cd /home/pi/screenly/ansible

sudo -E ansible-playbook site.yml "${EXTRA_ARGS[@]}"

# Pull down and install containers
/home/pi/screenly/bin/upgrade_containers.sh

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

sudo chown -R pi:pi /home/pi

# Run sudo w/out password
if [ ! -f /etc/sudoers.d/010_pi-nopasswd ]; then
  echo "pi ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/010_pi-nopasswd > /dev/null
  sudo chmod 0440 /etc/sudoers.d/010_pi-nopasswd
fi

# Ask user to set a new pi password if default password "raspberry" detected
check_defaultpw () {
    if [ "$BRANCH" = "master" ] || [ "$BRANCH" = "production" ] && [ "$WEB_UPGRADE" = false ]; then
        set +x

        # currently only looking for $6$/sha512 hash
        local VAR_CURRENTPISALT
        local VAR_CURRENTPIUSERPW
        local VAR_DEFAULTPIPW
        VAR_CURRENTPISALT=$(sudo cat /etc/shadow | grep pi | awk -F '$' '{print $3}')
        VAR_CURRENTPIUSERPW=$(sudo cat /etc/shadow | grep pi | awk -F ':' '{print $2}')
        VAR_DEFAULTPIPW=$(mkpasswd -m sha-512 raspberry "$VAR_CURRENTPISALT")

        if [[ "$VAR_CURRENTPIUSERPW" == "$VAR_DEFAULTPIPW" ]]; then
            echo "Warning: The default Raspberry Pi password was detected!"
            read -p "Do you still want to change it? (y/N)" -n 1 -r -s PWD_CHANGE
            if [ "$PWD_CHANGE" = 'y' ]; then
                set +e
                passwd
                set -ex
            fi
        else
            echo "The default raspberry pi password was not detected, continuing with installation..."
            set -x
        fi
    fi
}

check_defaultpw;

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
