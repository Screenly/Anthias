# Screenly OSE -- Digital Signage for the Raspberry Pi

When we started [Skarmverket](http://skarmverket.se), a small network of public billboards in Sweden, we evaluated many of the existing solutions on the market. Most of them were clunky and/or expensive. A fair number of them ran on Windows and used Adobe Flash. We ended up writing our own solution and used Asus Eee PCs with Linux. 

Then the [Raspberry Pi](http://www.raspberrypi.org/) came along. Priced at $35, and equipped with everything needed to power a screen. We were immediatelly sold and knew that this was what we wanted to use moving forward. Since we love Open Source, we've decided to release Screenly for free for anyone to use. 

The current version should be consider a beta. While we have been running it in the lab without any issues, there might still be issues that we are unaware of at this point. Yet, we wanted to make Screenly available to the vibrant Rasberry Pi community.

Since Screenly was written for the Raspberry Pi from the ground up, we had to make it as lean as possible. 

There are many use cases where Screenly can be used, such as:

 * Display BI or server dashboards
 * Advertisements (in-store or in store-front)
 * Internal information boards
 * Fancy photo-frame

A rought video of Screenly in action is available [here](http://www.youtube.com/watch?v=yjDjEfhspxk).

Screenshots and pictures of Screenly are available [here](https://picasaweb.google.com/102112347693505491575/Screenly01?authkey=Gv1sRgCNa2qp-j5vWUGQ).

## How Screenly works

Once installed, Screenly can view images, videos and websites on the screen. You can configure your own playlist, and set the duration for how long each element should be viewed.

Here's how you add content to your Screenly box:

 * Point your browser to the URL displayed on the screen at boot.
 * Click 'Add asset.'
  * Provide a name of the asset, the URL to the asset, and the asset type and click 'Submit.'
 * Click 'Schedule asset.'
  * Select the asset you just added in the drop-down, select the time frame you wish to display the asset and the duration (if image or website) and press 'Submit.'
 * Repeate for all the assets you want to display.

Note: If you don't have any server where you can make your asset available, you can use [public folders](https://www.dropbox.com/help/16/en) in Dropbox. 

## Requirements

 * A Raspberry Pi (Model B).
 * An SD Card (>2GB).
 * A HDMI-cable.
 * A network connection (with DHCP).
 * A keyboard and mouse (only required for the installation).
 * A monitor/TV that can view full HD (and has HDMI input).

## Configure the Raspberry Pi

 * Flash the SD card and install [Raspbian Wheezy](http://www.raspberrypi.org/downloads). Instructions are available [here](http://elinux.org/RPi_Easy_SD_Card_Setup).
 * Configure Raspbian to automatically log into X.
 * Make sure that the system's clock is configured for the proper timezone.
 * Expand the filesystem if needed. 

Please note that Screenly currently relies on the user 'pi', so don't change the username.

## Install Screenly OSE
 
Open a terminal-window (or SSH-session) and as the user 'pi' run:

    cd ~
    sudo apt-get update
    sudo apt-get -y install git-core
    git clone git://github.com/wireload/screenly-ose.git ~/screenly
    ~/screenly/misc/install.sh

Assuming everything went well, reboot your system. Screenly should now load. 

Upon boot, Screenly's URL should show up on the screen (e.g. http://aaa.bbb.ccc.ddd:8080).

## Supported media

Screenly currently three types of media:

 * Videos
  * Screenly uses [omxplayer](https://github.com/huceke/omxplayer/) as the video back-end. It is currently limited to MP4/h264-encoded videos.
 * Images
 * Web-pages

Adobe Flash-media *is not* supported. 

Images and web-pages will be rendered in 1920x1080, so adjust your content for this size. 

It is also worth noting that no media is permanently stored on the Raspberry Pi. All content is simply retrieved from the remote server (with limited caching in the browser).

## Upgrade Screenly

Since Screenly still is in beta, it's not unlikely that you'll run across bugs.

To upgrade Screenly, simply run (as the user 'pi'):

    cd ~/screenly
    git pull

Once done, simply restart the computer. If you prefer not to reboot, you might get away with (depending on the update):

    pkill -f "viewer.py"
    
## Licensing

Dual License: [GPLv2](http://www.gnu.org/licenses/gpl-2.0.html) and Commercial License. For more information, contact [WireLoad](http://wireload.net/company/). 
