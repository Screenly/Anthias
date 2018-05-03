#!/bin/bash

# Add GPU memory
echo -e "\n# Lock down GPU memory usage\ngpu_mem_256=96\ngpu_mem_512=128\ngpu_mem_1024=196\n" >> /boot/config.txt

# Install filesystem resizer
wget -O /etc/init.d/resize2fs_once https://github.com/RPi-Distro/pi-gen/raw/dev/stage2/01-sys-tweaks/files/resize2fs_once
chmod +x /etc/init.d/resize2fs_once
systemctl enable resize2fs_once
echo -n "init=/usr/lib/raspi-config/init_resize.sh" >> /boot/cmdline.txt

# Clean the apt cache
apt-get clean
