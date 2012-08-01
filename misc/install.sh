#!/bin/bash

echo "Installing Screenly OSE (alpha)"

echo "Installing dependencies..."
sudo apt-get -y install git-core python-pip python-netifaces python-simplejson python-imaging uzbl unclutter sqlite3 supervisor omxplayer x11-xserver-utils
sudo pip install bottle requests pytz hurry.filesize

echo "Adding Screenly to X auto start..."
mkdir -p ~/.config/lxsession/LXDE/
echo "@~/screenly/misc/xloader.sh" > ~/.config/lxsession/LXDE/autostart

echo "Increasing swap space to 500MB..."
echo "CONF_SWAPSIZE=500" > ~/dphys-swapfile
sudo cp /etc/dphys-swapfile /etc/dphys-swapfile.bak
sudo mv ~/dphys-swapfile /etc/dphys-swapfile

echo "Adding Screenly to autostart (via Supervisord)"
sudo ln -s ~/screenly/misc/screenly.conf /etc/supervisor/conf.d/
sudo /etc/init.d/supervisor stop
sudo /etc/init.d/supervisor start

echo "Making modifications to X..."
rm -f ~/.gtkrc-2.0
ln -s ~/screenly/misc/gtkrc-2.0 ~/.gtkrc-2.0
mv ~/.config/openbox/lxde-rc.xml ~/.config/openbox/lxde-rc.xml.bak
ln -s ~/screenly/misc/lxde-rc.xml ~/.config/openbox/lxde-rc.xml
mv ~/.config/lxpanel/LXDE/panels/panel ~/.config/lxpanel/LXDE/panels/panel.bak
sudo mv /etc/xdg/lxsession/LXDE/autostart /etc/xdg/lxsession/LXDE/autostart.bak

echo "Quiet the boot process..."
sudo cp /boot/cmdline.txt /boot/cmdline.txt.bak
sudo sed 's/$/ quiet/' -i /boot/cmdline.txt  

echo "Assuming no errors were encountered, go ahead and restart your computer."
