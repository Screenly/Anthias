#!/bin/bash

# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- sh-basic-offset: 4 -*-

set -euo pipefail

IS_CONNECTED=''

if [[ -z $(nmcli device wifi list) ]]; then
    echo "No Wi-Fi adapters were detected. Exiting..."
    exit 0
fi

if [[ ! -z $CHECK_CONN_FREQ ]]
    then
        freq=$CHECK_CONN_FREQ
    else
        freq=120
fi


sleep 5

while [[ true ]]; do
    echo "Checking internet connectivity ..."
    wget --spider --no-check-certificate 1.1.1.1 > /dev/null 2>&1

    if [ $? -eq 0 ]; then
        echo "Your device is already connected to the internet."
        echo "Skipping setting up Wifi-Connect Access Point."

        if [[ "$IS_CONNECTED" = 'false' ]]; then
            python send_zmq_message.py --action='show_splash'
        fi

        exit 0
    else
        echo "Your device is not connected to the internet."
        echo "Starting up Wifi-Connect."
        echo "Connect to the Access Point and configure the SSID and Passphrase for the network to connect to."

        if [[ "$IS_CONNECTED" = '' ]]; then
            python send_zmq_message.py --action='setup_wifi'
        fi

        IS_CONNECTED='false'

        /usr/src/app/wifi-connect -u /usr/src/app/ui
    fi

    sleep $freq
done
