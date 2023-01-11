#!/bin/bash

if [[ "$TARGET_PLATFORM" = 'linux/arm/v6' ]]; then
    architecture='rpi'
else
    architecture='armv7hf'
fi

wc_download_url='https://api.github.com/repos/balena-os/wifi-connect/releases/45509064'
jq_filter=".assets[] | select (.name|test(\"linux-$architecture\")) | .browser_download_url"
archive_url=$(curl -sL $wc_download_url | jq -r "$jq_filter")
archive_file=$(basename $archive_url)

wget $archive_url
tar -xvz -C /usr/src/app -f $archive_file
