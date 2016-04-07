#!/bin/bash
set -xe
sudo apt-get update
sudo apt-get install -y python-dev python-pip git-core
sudo pip install ansible==2.0.1.0

ansible localhost -m git -a "repo=${1:-git://github.com/wireload/screenly-ose.git} dest=/home/pi/screenly version=${2:-master}"
cd /home/pi/screenly/misc/ansible
ansible-playbook system.yml
ansible-playbook screenly.yml
