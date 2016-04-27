#!/bin/bash
set -xe
sudo apt-get update
sudo apt-get install -y python-dev python-setuptools git-core
sudo easy_install pip
sudo pip install ansible==2.0.2.0

ansible localhost -m git -a "repo=${1:-http://github.com/wireload/screenly-ose.git} dest=/home/pi/screenly version=${2:-master}"
cd /home/pi/screenly/ansible

read -p "Would you like to perform a full system upgrade as well? (y/N)" -n 1 -r -s
echo
if ! [[ $REPLY =~ ^[Yy]$  ]]; then
  ansible-playbook site.yml
else
  ansible-playbook site.yml --skip-tags "system-upgrade"
fi
