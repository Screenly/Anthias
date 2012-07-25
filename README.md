# Screenly Open Source Edition (OSE) -- Beta

Screenly is an intelligent digital signage solution for the [Raspberry Pi](http://www.raspberrypi.org/).
Once installed, the system can be managed remotely using a web-browser.

## Requirements

 * A Raspberry Pi (Model B) with [Raspbian Wheezy](http://www.raspberrypi.org/downloads).
 * An SD Card (>2GB).
 * A network connection (with DHCP).

## Configure Raspbian

 * Flash the SD card and install Raspbian Wheezy. Instructions are available [here](http://elinux.org/RPi_Easy_SD_Card_Setup).
 * Configure Raspbian to automatically log into X.
 * Make sure that the system's clock is configured for the proper timezone.
 * Expand the filesystem if needed. 

Please note that Screenly currently relies on the user 'pi', so don't change the username.

## Install Screenly OSE
 
Open a terminal and run:

    wget -O ~/install_screenly.sh https://github.com/wireload/screenly-ose/.....
    chmod +x install_screenly.sh
    ~/install_screenly.sh

Assuming everything went well, reboot your system. Screenly should now load. Upon loading, Screenly's URL should show up on the screen (http://<the IP>:8080).

## Supported media

Screenly currently three types of media:

 * Videos (all formats supported by [omxplayer](https://github.com/huceke/omxplayer/).)
 * Images
 * Web-pages

Images and web-pages will be rendered in a 1920x1080, so adjust your content for this size.
