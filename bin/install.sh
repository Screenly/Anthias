#!/bin/bash -xe

sudo apt-get update
sudo apt-get install -y python-dev python-setuptools git-core
sudo easy_install pip
sudo pip install ansible==2.0.2.0

ansible localhost -m git -a "repo=${1:-http://github.com/wireload/screenly-ose.git} dest=/home/pi/screenly version=${2:-master}"
cd /home/pi/screenly/ansible

set +x
read -p "Would you like to perform a full system upgrade as well? (y/N)" -n 1 -r -s && echo
if ! [[ $REPLY =~ ^[Yy]$  ]]; then
  set -e
  ansible-playbook site.yml --skip-tags "system-upgrade"
else
  set -e
  ansible-playbook site.yml
fi
