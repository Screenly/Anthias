#!/bin/bash -e

while :; do
    case $1 in
        add)
            sudo -u ${USER} /usr/local/bin/celery call --workdir /home/${USER}/screenly -A server.celery --args=[\"$2\"] server.append_usb_assets
            exit 0
            ;;
        remove)
            sudo -u ${USER} /usr/local/bin/celery call --workdir /home/${USER}/screenly -A server.celery --args=[\"$2\"] server.remove_usb_assets
            exit 0
            ;;
        *)
            exit 1
    esac
done
