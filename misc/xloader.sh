#!/bin/bash

LOG=/tmp/screenly_xloader.log

echo "Disabling screen power savings..." > $LOG

xset s off         # don't activate screensaver
xset -dpms         # disable DPMS (Energy Star) features.
xset s noblank     # don't blank the video device

sleep 5

echo "Launching infinite loop..." >> $LOG
while true
do
	# Clean up in case of an unclean exit
	echo "Cleaning up..." >> $LOG
	killall uzbl-core
	rm /tmp/uzbl_*
	rm /tmp/screenly_html/*
    
	# Launch the viewer
	python ~/screenly/viewer.py >> $LOG 2>&1
done
