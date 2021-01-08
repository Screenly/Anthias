#!/bin/bash

TMPFILE=/tmp/cec.log

# Clean up old file
rm -f $TMPFILE

read -p "What is the TV brand/make? " -r TV_MAKE
read -p "What is the TV model? " -r TV_MODEL

echo "TV Brand: $TV_MAKE" >> $TMPFILE
echo "TV Model: $TV_MODEL" >> $TMPFILE

echo -e "\n\nStarting diagnostics. This will take a few minutes...\n\n"

apt-get -qq update
apt-get install -yqq cec-utils pastebinit

echo -e "\n\nPerforming CEC 'scan' command:\n\n" >> $TMPFILE
echo 'scan' | cec-client -s -d 1 >> $TMPFILE

echo -e "\n\nPerforming CEC 'pow 0.0.0.0' command:\n\n" >> $TMPFILE
echo 'pow 0.0.0.0' | cec-client -s -d 1 >> $TMPFILE

read -p "Is the TV on right now? " -r TV_STATUS_START
echo "Is the TV on now? $TV_STATUS_START" >> $TMPFILE

echo -e "\n\nPerforming CEC 'standby 0.0.0.0' command:\n\n" >> $TMPFILE
echo 'standby 0.0.0.0' | cec-client -s -d 1 >> $TMPFILE

read -p "Is the TV off right now? " -r TV_STATUS_AFTER_POWER_OFF
echo "Is the TV off now? $TV_STATUS_AFTER_POWER_OFF" >> $TMPFILE

echo -e "\n\nPerforming CEC 'on 0.0.0.0' command:\n\n" >> $TMPFILE
echo 'on 0.0.0.0' | cec-client -s -d 1 >> $TMPFILE

read -p "Is the TV on right now? " -r TV_STATUS_AFTER_POWER_ON
echo "Is the TV on now? $TV_STATUS_AFTER_POWER_ON" >> $TMPFILE

echo -e "\n\nPerforming 'cec-compliance -A'  command:\n\n" >> $TMPFILE
cec-compliance -A >> $TMPFILE 2>&1

echo -e "\n\nPlease share this URL with us:"
pastebinit -P -i $TMPFILE 2> /dev/null
