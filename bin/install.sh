#!/bin/bash -e

echo -e "Screenly OSE is expected to run on a dedicated Raspberry Pi / SD card.\nYou will not be able to use the regular desktop environment once installed.\n"
read -p "Do you still want to continue? (y/N)" -n 1 -r -s
if ! [[ $REPLY =~ ^[Yy]$  ]]; then
  exit 1
fi

echo && read -p "Would you like to perform a full system upgrade as well? (y/N)" -n 1 -r -s && echo
if ! [[ $REPLY =~ ^[Yy]$  ]]; then
  EXTRA_ARGS="--skip-tags system-upgrade"
else
  EXTRA_ARGS=
fi

set -x
sudo mkdir -p /etc/ansible
echo -e "[local]\nlocalhost ansible_connection=local" | tee /etc/ansible/hosts

sudo apt-get update
sudo apt-get install -y python-dev python-setuptools git-core
sudo easy_install pip
sudo pip install ansible==2.0.2.0

ansible localhost -m git -a "repo=${1:-http://github.com/wireload/screenly-ose.git} dest=/home/pi/screenly version=${2:-master}"
cd /home/pi/screenly/ansible

ansible-playbook site.yml $EXTRA_ARGS
