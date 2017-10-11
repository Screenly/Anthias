# Screenly OSE Disk Image steps

 * Download the latest Rasbian [Stretch Lite](https://www.raspberrypi.org/downloads/raspbian/)
 * Flash out the disk image to a 4GB SD card
 * Boot the system
  * Run `raspi-config` and set the keyboard to `US English` and locale to `en_US.UTF-8 UTF-8`
  * Run the installer
  * Verify that everything works
  * Run `apt-get clean`
  * Shut down the system
 * Create the disk image
