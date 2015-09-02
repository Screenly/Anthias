#!/bin/bash

echo "Installing PiSign"

## Simple disk storage check. Naively assumes root partition holds all system data.
ROOT_AVAIL=$(df -k / | tail -n 1 | awk {'print $4'})
MIN_REQ="512000"

if [ $ROOT_AVAIL -lt $MIN_REQ ]; then
	echo "Insufficient disk space. Make sure you have at least 500MB available on the root partition."
	exit 1
fi

echo "Updating system package database..."
sudo apt-get -qq update > /dev/null

echo "Upgrading the system..."
echo "(This might take a while.)"
sudo apt-get -y -qq upgrade > /dev/null

echo "Installing dependencies..."
sudo apt-get -y -qq install git-core python-pip python-netifaces python-simplejson python-imaging python-dev uzbl sqlite3 supervisor omxplayer x11-xserver-utils libx11-dev watchdog chkconfig feh libffi-dev libwebkitgtk-3.0-dev libsoup2.4-dev python3-pip > /dev/null

echo "Downloading PiSign..."
git clone git://github.com/jameskirsop/pisign.git "$HOME/pisign" > /dev/null

echo "Downloading UZBL Source..."
git clone https://github.com/uzbl/uzbl.git "$HOME/uzbl-next" > /dev/null

echo "Installing more dependencies..."
sudo pip install -r "$HOME/pisign/requirements.txt" -q > /dev/null

echo "Making SSL Discovery Work"
sudo pip install requests[security]

echo "Installing Python3 Packages"
sudo pip-3.2 install six

echo "Adding PiSign to X auto start..."
mkdir -p "$HOME/.config/lxsession/LXDE-pi/"
echo "@$HOME/pisign/misc/xloader.sh" > "$HOME/.config/lxsession/LXDE-pi/autostart"

echo "Increasing swap space to 500MB..."
echo "CONF_SWAPSIZE=500" > "$HOME/dphys-swapfile"
sudo cp /etc/dphys-swapfile /etc/dphys-swapfile.bak
sudo mv "$HOME/dphys-swapfile" /etc/dphys-swapfile

echo "Adding PiSign's config-file"
mkdir -p "$HOME/.pisign"
cp "$HOME/pisign/misc/pisign.conf" "$HOME/.pisign/"

echo "Enabling Watchdog..."
sudo modprobe bcm2708_wdog > /dev/null
sudo cp /etc/modules /etc/modules.bak
sudo sed '$ i\bcm2708_wdog' -i /etc/modules
sudo chkconfig watchdog on
sudo cp /etc/watchdog.conf /etc/watchdog.conf.bak
sudo sed -e 's/#watchdog-device/watchdog-device/g' -i /etc/watchdog.conf
sudo /etc/init.d/watchdog start

echo "Adding PiSign to autostart (via Supervisord)"
sudo ln -s "$HOME/pisign/misc/supervisor_pisign.conf" /etc/supervisor/conf.d/pisign.conf
sudo /etc/init.d/supervisor stop > /dev/null
sudo /etc/init.d/supervisor start > /dev/null

echo "Making modifications to X..."
[ -f "$HOME/.gtkrc-2.0" ] && rm -f "$HOME/.gtkrc-2.0"
ln -s "$HOME/pisign/misc/gtkrc-2.0" "$HOME/.gtkrc-2.0"
[ -f "$HOME/.config/openbox/lxde-rc.xml" ] && mv "$HOME/.config/openbox/lxde-rc.xml" "$HOME/.config/openbox/lxde-rc.xml.bak"
[ -d "$HOME/.config/openbox" ] || mkdir -p "$HOME/.config/openbox"
ln -s "$HOME/pisign/misc/lxde-rc.xml" "$HOME/.config/openbox/lxde-pi-rc.xml"
[ -f "$HOME/.config/lxpanel/LXDE-pi/panels/panel" ] && mv "$HOME/.config/lxpanel/LXDE-pi/panels/panel" "$HOME/.config/lxpanel/LXDE-pi/panels/panel.bak"
[ -f /etc/xdg/lxsession/LXDE/autostart ] && sudo mv /etc/xdg/lxsession/LXDE/autostart /etc/xdg/lxsession/LXDE/autostart.bak
[ -f "/etc/xdg/lxsession/LXDE-pi/autostart" ] && sudo mv "/etc/xdg/lxsession/LXDE-pi/autostart" "/etc/xdg/lxsession/LXDE-pi/autostart.bak"
sudo sed -e 's/^#xserver-command=X$/xserver-command=X -nocursor -s 0 dpms/g' -i /etc/lightdm/lightdm.conf

echo "Setting uzbl browser to start in full screen mode"
sudo sed -i '/<\/applications>/ i\ <application name="uzbl*">\n<fullscreen>yes</fullscreen>\n</application>' $HOME/.config/openbox/lxde-pi-rc.xml

# echo "Removing default lxpanel profile"
# sed -i 's/^@lxpanel/#&/' /etc/xdg/lxsession/LXDE-pi/autostart

# Make sure we have proper framebuffer depth.
if grep -q framebuffer_depth /boot/config.txt; then
  sudo sed 's/^framebuffer_depth.*/framebuffer_depth=32/' -i /boot/config.txt
else
  echo 'framebuffer_depth=32' | sudo tee -a /boot/config.txt > /dev/null
fi

# Fix frame buffer bug
if grep -q framebuffer_ignore_alpha /boot/config.txt; then
  sudo sed 's/^framebuffer_ignore_alpha.*/framebuffer_ignore_alpha=1/' -i /boot/config.txt
else
  echo 'framebuffer_ignore_alpha=1' | sudo tee -a /boot/config.txt > /dev/null
fi

echo "Quiet the boot process..."
sudo cp /boot/cmdline.txt /boot/cmdline.txt.bak
sudo sed 's/$/ quiet/' -i /boot/cmdline.txt

echo "Assuming no errors were encountered, go ahead and restart your computer."
