#!/bin/bash

echo "Installing Screenly OSE (alpha)"

echo "Installing dependencies..."
sudo apt-get -y install git-core python-pip python-netifaces python-simplejson python-imaging uzbl unclutter sqlite3 supervisor omxplayer
sudo pip install bottle requests pytz hurry.filesize

echo "Cloning repository..."
cd ~
git clone git@github.com:wireload/screenly-ose.git ~/screenly2

echo "Adding Screenly to X auto start..."
echo "@~/screenly2/misc/xloader.sh" > ~/.config/lxsession/LXDE/autostart

echo "Increasing swap space to 500MB..."
echo "CONF_SWAPSIZE=500" > ~/dphys-swapfile
sudo cp /etc/dphys-swapfile /etc/dphys-swapfile.bak
sudo mv ~/dphys-swapfile /etc/dphys-swapfile

echo "Adding Screenly to autostart (via Supervisord)"
sudo ln -s ~/screenly2/misc/screenly.conf /etc/supervisor/conf.d/
sudo /etc/init.d/supervisor stop
sudo /etc/init.d/supervisor start

echo "Making modifications to X..."
ln -s ~/screenly2/misc/gtkrc-2.0 ~/.gtkrc-2.0
mv ~/.config/openbox/lxde-rc.xml ~/.config/openbox/lxde-rc.xml.bak
ln -s ~/screenly2/misc/lxde-rc.xml ~/.config/openbox/lxde-rc.xml

echo "Assuming no errors were encountered, go ahead and restart your computer."
