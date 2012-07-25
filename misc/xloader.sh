#!/bin/bash

xset s off         # don't activate screensaver
xset -dpms         # disable DPMS (Energy Star) features.
xset s noblank     # don't blank the video device

sleep 5
while true
do
    # Clean up in case of an unclean exit
    killall uzbl-core
    rm /tmp/uzbl_*
    rm /tmp/screenly_html/*
    
    # Launch the viewer
    python ~/screenly/viewer.py
done
