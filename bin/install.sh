#!/bin/bash
set -xe
sudo apt-get update
sudo apt-get install -y python-dev python-setuptools git-core
sudo easy_install pip
sudo pip install ansible==2.0.2.0

ansible localhost -m git -a "repo=${1:-http://github.com/wireload/screenly-ose.git} dest=/home/pi/screenly version=${2:-master}"
cd /home/pi/screenly/ansible
ansible-playbook site.yml
