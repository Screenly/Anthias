#!/bin/bash

LOG=/tmp/screenly_xloader.log

echo "Disabling screen power savings..." > $LOG

xset s off          # Don't activate screensaver
xset -dpms          # Disable DPMS (Energy Star) features
xset s noblank      # Don't blank the video device
export NOREFRESH=1  # Fix white flickering in omxplayer

# Wait for server.py to be up and running (maximum 25s)
RETRIES=1
MAXRETRIES=5
while [[ $RETRIES < $MAXRETRIES ]]; do
  echo "Waiting for server.py to start (retry $RETRIES / $MAXRETRIES)" >> $LOG
  SERVERPID=$(pgrep -f server.py)
  if [ -x $SERVERPID ]; then
    RETRIES=$(($RETRIES+1))
    sleep 5
  else
    RETRIES=$MAXRETRIES
  fi
done

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
