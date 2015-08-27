#!/bin/bash

LOG=/tmp/screenly_xloader.log

echo "Disabling screen power savings..." > $LOG

xset s off                         # Don't activate screensaver
xset -dpms                         # Disable DPMS (Energy Star) features
xset s noblank                     # Don't blank the video device

sleep 5

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
	python ~/pisign/viewer.py >> $LOG 2>&1
done
