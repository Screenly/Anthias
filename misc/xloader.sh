#!/bin/bash

LOG=/tmp/screenly_xloader.log

echo "Disabling screen power savings..." > $LOG

xset s off         # don't activate screensaver
xset -dpms         # disable DPMS (Energy Star) features.
xset s noblank     # don't blank the video device

sleep 5

# Initialization block for Screenly Pro
if [ -f /home/pi/.screenly_not_initialized ] && [ -f /home/pi/screenly/setup.py ]; then
	python ~/screenly/setup.py >> $LOG 2>&1 &
fi

echo "Launching infinite loop..." >> $LOG
while true
do
	# Clean up in case of an unclean exit
	echo "Cleaning up..." >> $LOG
	killall uzbl-core
	killall omxplayer omxplayer.bin
	rm -f /tmp/uzbl_*
	rm -f /tmp/screenly_html/*

	# Launch the viewer
	python ~/screenly/viewer.py >> $LOG 2>&1
done
