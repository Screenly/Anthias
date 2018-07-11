# Screenly OSE Disk Image steps

 * Download the latest Rasbian [Stretch Lite](https://www.raspberrypi.org/downloads/raspbian/)
 * Flash out the disk image to a 4GB SD card
 * Boot the system
  * Run `raspi-config` and:
    * Set the keyboard to locale to `en_US.UTF-8 UTF-8`
  * Run the installer and install the production branch
  * Verify that everything works
  * Run `sudo ./bin/prepare_device_for_imaging.sh`
  * Shut down the system
 * Create the disk image by running `SD_DEV=/dev/sdc sudo -E ./bin/create_screenly_ose_build.sh` on a Linux machine (where `/dev/sdc` is your SD card)
