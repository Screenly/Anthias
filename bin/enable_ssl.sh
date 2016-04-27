#!/bin/bash -ex

cd ~/screenly/ansible
ansible-playbook -t enable-ssl site.yml

set +x
echo "You should be all set. You should be able to access Screenly's management interface at https://<your IP>"

echo "NOTE: If you have the firewall enabled, make sure to open it up for HTTPS (port 443)."
echo "Assuming you're running UFW, the command is:"
echo "sudo ufw allow 443/tcp"
