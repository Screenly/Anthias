#!/bin/bash

# Add the sample assets
start_date=$(date '+%FT%H:%M:%SZ')
end_date=$(date '+%FT%H:%M:%SZ' -d '+6 years')

curl --header "Content-Type: application/json" \
  --request POST \
  --data "{
    \"name\": \"Screenly Weather Widget\",
    \"uri\": \"https://weather.srly.io\",
    \"mimetype\": \"webpage\",
    \"start_date\": \"$start_date\",
    \"end_date\": \"$end_date\",
    \"play_order\": 0,
    \"is_enabled\": 1,
    \"skip_asset_check\": 0
  }" \
  http://127.0.0.1:8080/api/v1.2/assets

curl --header "Content-Type: application/json" \
  --request POST \
  --data "{
    \"name\": \"Screenly Clock Widget\",
    \"uri\": \"https://clock.srly.io\",
    \"mimetype\": \"webpage\",
    \"start_date\": \"$start_date\",
    \"end_date\": \"$end_date\",
    \"play_order\": 1,
    \"is_enabled\": 1,
    \"skip_asset_check\": 0
  }" \
  http://127.0.0.1:8080/api/v1.2/assets

curl --header "Content-Type: application/json" \
  --request POST \
  --data "{
    \"name\": \"Hacker News\",
    \"uri\": \"https://news.ycombinator.com\",
    \"mimetype\": \"webpage\",
    \"start_date\": \"$start_date\",
    \"end_date\": \"$end_date\",
    \"play_order\": 2,
    \"is_enabled\": 1,
    \"skip_asset_check\": 0
  }" \
  http://127.0.0.1:8080/api/v1.2/assets

# Add GPU memory
echo -e "\n# Lock down GPU memory usage\ngpu_mem_256=96\ngpu_mem_512=128\ngpu_mem_1024=196\n" >> /boot/config.txt

# Install filesystem resizer
wget -O /etc/init.d/resize2fs_once https://github.com/RPi-Distro/pi-gen/blob/master/stage2/01-sys-tweaks/files/resize2fs_once
chmod +x /etc/init.d/resize2fs_once
systemctl enable resize2fs_once
sed -i '$s/$/ init=\/usr\/lib\/raspi-config\/init_resize.sh/' /boot/cmdline.txt

# Clean the apt cache
apt-get clean
