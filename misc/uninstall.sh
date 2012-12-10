#!/bin/bash

echo "Uninstalling Screenly OSE (beta)"

echo "Removing Screenly from X auto start..."
sed "/screenly/d" /home/pi/.config/lxsession/LXDE/autostart > /home/pi/.config/lxsession/LXDE/autostart

echo "Deleting Screenly Directory"
rm -r -f ~/.screenly

echo "Disabling Watchdog..."
sudo /etc/init.d/watchdog stop
sudo chkconfig watchdog off


echo "Removing Screenly from autostart (via Supervisord)"
sudo rm -f /etc/supervisor/conf.d/supervisor_screenly.conf
sudo /etc/init.d/supervisor stop
sudo /etc/init.d/supervisor start

echo "Unquiet the boot process..."
sudo rm -f /boot/cmdline.txt
sudo mv /boot/cmdline.txt.bak /boot/cmdline.txt

echo "Assuming no errors were encountered, go ahead and restart your computer."
