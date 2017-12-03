# Screenly OSE Disk Image steps

 * Download the latest Rasbian [Stretch Lite](https://www.raspberrypi.org/downloads/raspbian/)
 * Flash out the disk image to a 4GB SD card
 * Boot the system
  * Run `sudo apt-get update && sudo apt-get -y upgrade`
  * Run `raspi-config` and:
    * Set the keyboard to locale to `en_US.UTF-8 UTF-8`
    * Set the memory split to 192
  * Run the installer and install the production branch
  * Verify that everything works
  * Run `apt-get clean`
  * Shut down the system
 * Create the disk image
