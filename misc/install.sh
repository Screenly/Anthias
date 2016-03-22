#!/bin/bash
set -xe
sudo apt-get update
sudo apt-get install -y python-dev python-pip git-core
sudo pip install ansible==2.0.1.0

ansible localhost -m git -a "repo=git://github.com/over64/screenly-ose.git dest=/home/pi/screenly"
cd /home/pi/screenly/misc/ansible
ansible-playbook system.yml
ansible-playbook screenly.yml
