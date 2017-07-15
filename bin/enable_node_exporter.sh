#!/bin/bash -ex

cd ~/screenly/ansible
ansible-playbook -t enable-ssl site.yml

set +x
echo "You should be all set. You should be able to access monitor the device using Prometheus at http://<your IP>:9100/metrics"

echo "NOTE: If you have the firewall enabled, make sure to open it up."
echo "Assuming you're running UFW, the command is:"
echo "sudo ufw allow 9100/tcp"
