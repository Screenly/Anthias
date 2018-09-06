#!/usr/bin/env bash

recover(){
    service screenly-viewer stop
    rm -rf "$2/.screenly" "$2/screenly_assets"
    tar -xvf "$1" -C "$2"
    service screenly-viewer start
}

cleanup(){
    find ~/screenly_assets/ -name '*.tmp' -delete
}

while :; do
    case $1 in
        recover)
            if [ -f "$2" ]; then
                recover "$2" "$3"
                shift
                exit 0
            else
                echo "The backup file does not exist."
                exit 1
            fi
            ;;
        cleanup)
            cleanup
            exit 0
            ;;
        *)
            echo "Invalid command"
            exit 1
    esac
done
