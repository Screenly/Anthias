#!/bin/bash -e

if [ $1 = "latest" ]; then
    export DOCKER_TAG="latest"
    BRANCH="master"
#elif [ $1 = "experimental" ]; then
#  export DOCKER_TAG="experimental"
#  BRANCH="experimental"
#elif [ $1 = "production" ]; then
#    export DOCKER_TAG="production"
#    BRANCH="production"
else
  exit 1
fi


if [ $2 = false ]; then
  export MANAGE_NETWORK=false
elif [ $2 = true ]; then
  dpkg -s network-manager > /dev/null 2>&1
  if [ "$?" = "1" ]; then
    echo -e "\n\nIt looks like NetworkManager is not installed. Please install it by running 'sudo apt install -y network-manager' and then re-run the installation."
    exit 1
  fi
  export MANAGE_NETWORK=true
else
  exit 1
fi


if [ $3 = false ]; then
  EXTRA_ARGS="--skip-tags enable-ssl,system-upgrade"
elif [ $3 = true ]; then
  EXTRA_ARGS="--skip-tags enable-ssl"
else
  exit 1
fi


if grep -qF "Raspberry Pi 3" /proc/device-tree/model; then
   export DEVICE_TYPE="pi3"
elif grep -qF "Raspberry Pi 2" /proc/device-tree/model; then
   export DEVICE_TYPE="pi2"
else
   export DEVICE_TYPE="pi1"
fi

export HOME=/home/pi

set -e
sudo mkdir -p /etc/ansible
echo -e "[local]\nlocalhost ansible_connection=local" | sudo tee /etc/ansible/hosts > /dev/null


if [ ! -f /etc/locale.gen ]; then
  # No locales found. Creating locales with default UK/US setup.
  echo -e "en_GB.UTF-8 UTF-8\nen_US.UTF-8 UTF-8" | sudo tee /etc/locale.gen > /dev/null
  sudo locale-gen
fi


sudo sed -i 's/apt.screenlyapp.com/archive.raspbian.org/g' /etc/apt/sources.list
sudo apt-get update
sudo apt-get purge -y python-setuptools python-pip python-pyasn1
sudo apt-get install -y python-dev git-core libffi-dev libssl-dev
curl -s https://bootstrap.pypa.io/get-pip.py | sudo python

sudo pip install ansible==2.7.1

ansible localhost -m git -a "repo=${1:-https://github.com/screenly/screenly-ose.git} dest=/home/pi/screenly version=$BRANCH"
cd /home/pi/screenly/ansible

ansible-playbook site.yml $EXTRA_ARGS

sudo apt-get autoclean
sudo apt-get clean
sudo find /usr/share/doc -depth -type f ! -name copyright -delete
sudo find /usr/share/doc -empty -delete
sudo rm -rf /usr/share/man /usr/share/groff /usr/share/info /usr/share/lintian /usr/share/linda /var/cache/man
sudo find /usr/share/locale -type f ! -name 'en' ! -name 'de*' ! -name 'es*' ! -name 'ja*' ! -name 'fr*' ! -name 'zh*' -delete
sudo find /usr/share/locale -mindepth 1 -maxdepth 1 ! -name 'en*' ! -name 'de*' ! -name 'es*' ! -name 'ja*' ! -name 'fr*' ! -name 'zh*' -exec rm -r {} \;

cd ~/screenly && git rev-parse HEAD > ~/.screenly/latest_screenly_sha

sudo chown -R pi:pi /home/pi

set +e

echo "Installation completed. You need to reboot the system for the installation to complete."
exit 0
