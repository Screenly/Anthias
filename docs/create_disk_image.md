# Screenly OSE Disk Image steps

 * Download the latest Rasbian [Stretch Lite](https://www.raspberrypi.org/downloads/raspbian/)
 * Flash out the disk image to a 4GB SD card
 * Boot the system
  * Run `raspi-config` and:
    * Set the keyboard to locale to `en_US.UTF-8 UTF-8`
  * Run the installer and install the production branch
  * Verify that everything works
  * Run `apt-get clean`
  * Run `sudo wget -O /etc/init.d/resize2fs_once https://github.com/RPi-Distro/pi-gen/raw/dev/stage2/01-sys-tweaks/files/resize2fs_once`
  * Run `sudo chmod +x /etc/init.d/resize2fs_once`
  * Run `sudo systemctl enable resize2fs_once`
  * Add `init=/usr/lib/raspi-config/init_resize.sh` in `/boot/cmdline.txt`
  * Shut down the system
 * Create the disk image
 * Use `SD_DEV=/dev/sdX bin/create_screenly_ose_build.sh` to generate the image
