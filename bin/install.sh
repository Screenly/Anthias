#!/bin/bash -e

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

echo && read -p "Would you like to use the development branch? You will get the latest features, but things may break. (y/N)" -n 1 -r -s DEV && echo
if [ "$DEV" != 'y'  ]; then
  BRANCH="production"
else
  BRANCH="master"
fi

echo && read -p "Would you like to perform a full system upgrade as well? (y/N)" -n 1 -r -s UPGRADE && echo
if [ "$UPGRADE" != 'y' ]; then
  EXTRA_ARGS="--skip-tags enable-ssl,system-upgrade"
else
  EXTRA_ARGS="--skip-tags enable-ssl"
fi

set -x
sudo mkdir -p /etc/ansible
echo -e "[local]\nlocalhost ansible_connection=local" | sudo tee /etc/ansible/hosts > /dev/null

if [ ! -f /etc/locale.gen ]; then
  # No locales found. Creating locales with default UK/US setup.
  echo -e "en_GB.UTF-8 UTF-8\nen_US.UTF-8 UTF-8" | sudo tee /etc/locale.gen > /dev/null
  sudo locale-gen
fi


sudo sed -i 's/apt.screenlyapp.com/archive.raspbian.org/g' /etc/apt/sources.list
sudo apt-get update
sudo apt-get remove -y python-setuptools python-pip
sudo apt-get install -y python-dev git-core libffi-dev libssl-dev
curl -s https://bootstrap.pypa.io/get-pip.py | sudo python
sudo pip install ansible==2.0.2.0

ansible localhost -m git -a "repo=${1:-http://github.com/wireload/screenly-ose.git} dest=/home/pi/screenly version=$BRANCH"
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

set +x
echo "Installation completed."
