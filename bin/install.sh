#!/bin/bash -xe

sudo apt-get update
sudo apt-get install -y python-dev python-setuptools git-core
sudo easy_install pip
sudo pip install ansible==2.0.2.0

ansible localhost -m git -a "repo=${1:-http://github.com/wireload/screenly-ose.git} dest=/home/pi/screenly version=${2:-master}"
cd /home/pi/screenly/ansible

set +x
read -p "Screenly OSE is expected to run on a dedicated Raspberry Pi / SD card.\nYou will not be able to use the regular desktop environment once installed. Do you still want to continue? (y/N)" -n 1 -r -s && echo
if ! [[ $REPLY =~ ^[Yy]$  ]]; then
  exit 1
fi

read -p "Would you like to perform a full system upgrade as well? (y/N)" -n 1 -r -s && echo
if ! [[ $REPLY =~ ^[Yy]$  ]]; then
  set -x
  ansible-playbook site.yml --skip-tags "system-upgrade"
else
  set -x
  ansible-playbook site.yml
fi
