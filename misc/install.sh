sudo apt-get update
sudo apt-get install -y python-setuptools python-dev git-core
sudo easy_install pip
sudo pip install ansible

ansible localhost -m git -a "repo=git://github.com/over64/screenly-ose.git dest=/home/pi/screenly version=master" || exit
cd /home/pi/screenly/misc/ansible
ansible-playbook system.yml || exit
ansible-playbook screenly.yml || exit
