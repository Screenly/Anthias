#!/bin/bash -ex

cd ~/screenly/ansible
ansible-playbook -t disable-ssl site.yml

set +x
echo "You should be all set. You should be able to access Screenly's management interface at http://<your IP>:<port>"
