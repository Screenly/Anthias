# Anthias - Digital Signage for the Raspberry Pi
[![Run Unit Tests](https://github.com/Screenly/Anthias/actions/workflows/docker-test.yaml/badge.svg)](https://github.com/Screenly/Anthias/actions/workflows/docker-test.yaml)
[![CodeQL](https://github.com/Screenly/Anthias/actions/workflows/codeql-analysis.yaml/badge.svg)](https://github.com/Screenly/Anthias/actions/workflows/codeql-analysis.yaml)
[![Lint Code Base](https://github.com/Screenly/Anthias/actions/workflows/linter.yml/badge.svg)](https://github.com/Screenly/Anthias/actions/workflows/linter.yml)

![Anthias Logo](https://github.com/Screenly/Anthias/blob/master/static/img/dark.svg?raw=true  "Anthias Logo")

## About Anthias

Anthias is a digital signage platform for Raspberry Pi. Formerly known as Screenly OSE, it was rebranded to clear up the confusion between Screenly (the paid version) and Anthias. More details can be found in [this blog post](https://www.screenly.io/blog/2022/12/06/screenly-ose-now-called-anthias/).

Want to help Anthias thrive? Support us using [GitHub Sponsor](https://github.com/sponsors/Screenly).

## Compatibility

We've tested Anthias and is known to work on the following Raspberry Pi models:

* Raspberry Pi 4 Model B - 32-bt and 64-bit Bullseye, 64-bit Bookworm
* Raspberry Pi 3 Model B+ - 32-bit and 64-bit Bullseye, 64-bit Bookworm
* Raspberry Pi 3 Model B - 64-bit Bookworm

We're still fixing the installer so that it'll work with Raspberry Pi Zero and Raspberry Pi 2.
Should you encounter any issues, please file an issue either in this repository or in the
[forums](https://forums.screenly.io).

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Screenly/Anthias&type=Date)](https://star-history.com/#Screenly/Anthias&Date)


## Balena disk images

The quickest way to get started is to use [Raspberry Pi Imager](https://www.screenly.io/blog/2022/12/13/anthias-and-screenly-now-in-rpi-imager/), where you can find Anthias under `Other specific-purpose OS`. Alternatively, you can find our pre-built disk images (powered by [Balena Hub](https://hub.balena.io/)) [here](https://github.com/Screenly/Anthias/releases/latest/).

Do however note that we are still in the process of knocking out some bugs. You can track the known issues [here](https://github.com/Screenly/Anthias/issues). You can also check the discussions in the [Anthias forums](https://forums.screenly.io).

If you'd like more control over your digital signage instance, try installing it on
[Raspberry Pi OS Lite](#installing-on-raspberry-pi-os-lite).

## Installing on Raspberry Pi OS Lite

The tl;dr for on [Raspberry Pi OS](https://www.raspberrypi.com/software/) Bullseye Lite is:

```
$ bash <(curl -sL https://install-anthias.srly.io)
```

If you've selected **_N_** when prompted for an upgrade &ndash; i.e., "Would you like to perform a full system upgrade as well? (y/N)"
&ndash; you'll get the following message when the installer is almost done executing:

```
"Please reboot and run /home/$USER/screenly/bin/upgrade_containers.sh to complete the installation. Would you like to reboot now? (y/N)"
```

You have the option to reboot now or later. On the next boot, make sure to run
`upgrade_containers.sh`, as mentioned above.

Otherwise, if you've selected **_y_** for the system upgrade, then you don't need to do a reboot for the containers to be started. However,
it's still recommended to do a reboot.

**This installation will take 15 minutes to several hours**, depending on variables such as:

 * The Raspberry Pi hardware version
 * The SD card
 * The internet connection

During ideal conditions (Raspberry Pi 3 Model B+, class 10 SD card and fast internet connection), the installation normally takes 15-30 minutes. On a Raspberry Pi Zero or Raspberry Pi Model B with a class 4 SD card, the installation will take hours.

## Installing with Balena

Go through the steps in [this documentation](/docs/balena-fleet-deployment.md)
to deploy Anthias on your Balena fleet.

## Quick links

 * [Forum](https://forums.screenly.io/)
 * [Website](https://anthias.screenly.io) (hosted on GitHub and the source is available [here](https://github.com/Screenly/Anthias/tree/master/website))
 * [General documentation](https://github.com/Screenly/Anthias/blob/master/docs/README.md)
 * [Developer documentation](https://github.com/Screenly/Anthias/blob/master/docs/developer-documentation.md)
