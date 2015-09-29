#!/bin/bash

LOG=/tmp/screenly_xloader.log

echo "Disabling screen power savings..." > $LOG

xset s off          # Don't activate screensaver
xset -dpms          # Disable DPMS (Energy Star) features
xset s noblank      # Don't blank the video device
export NOREFRESH=1  # Fix white flickering in omxplayer

# Wait for server.py to be up and running (maximum 28s)
echo "Waiting for server.py to start" >> $LOG
wget --retry-connrefused -t 8  http://0.0.0.0:8080/splash_page >> $LOG 2>&1

echo "Launching infinite loop..." >> $LOG
while true; do
	# Clean up in case of an unclean exit
	echo "Cleaning up..." >> $LOG
	killall uzbl-core
	killall omxplayer omxplayer.bin
	rm -f /tmp/uzbl_*
	rm -f /tmp/screenly_html/*

	# Launch the viewer
	python ~/screenly/viewer.py >> $LOG 2>&1
done
