#!/usr/bin/env bash

recover(){
    sudo service screenly-viewer stop
    rm -rf /home/pi/.screenly /home/pi/screenly_assets
    tar -xvf "$1" -C /home/pi/
    sudo service screenly-viewer start
}

while :; do
    case $1 in
        recover)
            if [ -f "$2" ]; then
                recover "$2"
                shift
                exit 0
            else
                echo "The backup file does not exist."
                exit 1
            fi
            ;;
        *)
            echo "Invalid command"
            exit 1
    esac
done
