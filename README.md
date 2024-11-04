# Anthias - Digital Signage for the Raspberry Pi
[![Run Unit Tests](https://github.com/Screenly/Anthias/actions/workflows/docker-test.yaml/badge.svg?branch=master)](https://github.com/Screenly/Anthias/actions/workflows/docker-test.yaml)
[![CodeQL](https://github.com/Screenly/Anthias/actions/workflows/codeql-analysis.yaml/badge.svg?branch=master)](https://github.com/Screenly/Anthias/actions/workflows/codeql-analysis.yaml)
[![Lint Python Code Base](https://github.com/Screenly/Anthias/actions/workflows/python-lint.yaml/badge.svg?branch=master)](https://github.com/Screenly/Anthias/actions/workflows/python-lint.yaml)
[![sbomified](https://sbomify.com/assets/images/logo/badge.svg)](https://app.sbomify.com/project/zZGXzm8qcU)

![Anthias Logo](https://github.com/Screenly/Anthias/blob/master/static/img/dark.svg?raw=true  "Anthias Logo")

## :sparkles: About Anthias

Anthias is a digital signage platform for Raspberry Pi and x86 devices. Formerly known as Screenly OSE, it was rebranded to clear up the confusion between Screenly (the paid version) and Anthias. More details can be found in [this blog post](https://www.screenly.io/blog/2022/12/06/screenly-ose-now-called-anthias/).

Want to help Anthias thrive? Support us using [GitHub Sponsor](https://github.com/sponsors/Screenly).

## :white_check_mark: Compatibility

We've tested Anthias and is known to work on the following Raspberry Pi models:

* Raspberry Pi 4 Model B - 32-bt and 64-bit Bullseye, 64-bit Bookworm
* Raspberry Pi 3 Model B+ - 32-bit and 64-bit Bullseye, 64-bit Bookworm
* Raspberry Pi 3 Model B - 64-bit Bookworm and Bullseye
* Raspberry Pi 2 Model B - 32-bit Bookworm and Bullseye
* x86 Devices - 64-bit Bookworm
    * These devices can be something similar to a NUC.

> [!NOTE]
> We're still fixing the installer so that it'll work with Raspberry Pi Zero and Raspberry Pi 1.
> Should you encounter any issues, please file an issue either in this repository or in the
[forums](https://forums.screenly.io).

## :star: Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Screenly/Anthias&type=Date)](https://star-history.com/#Screenly/Anthias&Date)

## :package: Installation options

### Using the image from the Raspberry Pi Imager

The quickest way to get started is to use [Raspberry Pi Imager](https://www.screenly.io/blog/2022/12/13/anthias-and-screenly-now-in-rpi-imager/), where you can find Anthias under `Other specific-purpose OS`.

![imager-01](./docs/images/imager-01.png)

![imager-02](./docs/images/imager-02.png)

![imager-03](./docs/images/imager-03.png)

### Using the images from balenaHub

> [!IMPORTANT]
> This option is recommended for those who want to install Anthias without touching the
> command line interface. When a new rolling release is available, updates will automatically
> be installed on your device.

Balena made a [big update to their IoT marketplace](https://blog.balena.io/creating-an-iot-marketplace/). Included in that change is the launch of
[Fleets for Good](https://hub.balena.io/fleets-for-good). With that, you may find it hard to find the Anthias images on the marketplace. In the meantime,
here are the links to the images:

* [Raspberry Pi 1](https://hub.balena.io/fleets-for-good/1971378/anthias-pi1)
* [Raspberry Pi 2](https://hub.balena.io/fleets-for-good/1971385/anthias-pi2)
* [Raspberry Pi 3](https://hub.balena.io/fleets-for-good/1971388/anthias-pi3)
* [Raspberry Pi 4](https://hub.balena.io/fleets-for-good/1971389/anthias-pi4)

Go to one of the links above and click the *Join* button, then select either *Ethernet only* or *Wifi + Ethernet* for Network options.
You can either click the *Flash* button to open balenaEthcher (make sure that it's installed) or download the image file and flash it using your preferred imager.
Flash the SD card and boot up your Raspberry Pi. It will take a few minutes to boot up and start the services.

Alternatively, you can [download our pre-built Balena disk images from the releases](#using-the-images-from-the-releases).

### Using the images from the releases

You can find the latest release [here](https://github.com/Screenly/Anthias/releases/latest). From there, you can download the disk image that you need and flash it to your SD card.
The image file looks something like `<yyyy>-<mm>-<dd>-raspberry<version>.zip`. Take note that the `.img` file is compressed in a `.zip` file.

Starting [v0.19.0](https://github.com/Screenly/Anthias/releases/tag/v0.19.0), devices installed using this option will be
pinned to the version that you've downloaded. This means that the devices will get updates when a new release (e.g., v0.19.1, etc.)
is available.

### Installing on Raspberry Pi OS Lite

#### Overview

If you'd like more control over your digital signage instance, try installing it on Raspberry Pi OS Lite.

> [!IMPORTANT]
> Before you start, make sure that you have `curl` installed. If not, you can install it by running:
> 
> ```bash
> $ sudo apt update
> $ sudo apt install -y curl
> ```

The tl;dr for on [Raspberry Pi OS](https://www.raspberrypi.com/software/) is:

```
$ bash <(curl -sL https://install-anthias.srly.io)
```

You'll be prompted with the following questions:

* Do you still want to continue?
* Would you like Anthias to manage the network for you?
* Which version of Anthias would you like to install?
* Would you like to perform a full system upgrade as well?

You can either use the arrow keys to select your choice and then press Enter or type `y` or `n`
(for yes-no questions). The installer will display your responses before proceeding with the
installation.

![install-anthias-gif](./docs/images/install-anthias.gif)

**This installation will take 15 minutes to several hours**, depending on variables such as:

 * The Raspberry Pi hardware version
 * The SD card
 * The internet connection

> [!NOTE]
> During ideal conditions (Raspberry Pi 3 Model B+, class 10 SD card and fast internet connection), the installation normally takes 15-30 minutes. On a Raspberry Pi Zero or Raspberry Pi Model B with a class 4 SD card, the installation will take hours.

#### Prompt: Network Management

Opting for network management will enable and configure the [NetworkManager](https://wiki.debian.org/NetworkManager) service on your device.

#### Prompt: Version Selection

You can choose between the following choices &mdash; `latest` and `tag`.

* Selecting `latest` will install the version from the `master` branch.
* Selecting `tag` will prompt you to enter a specific tag to install.
* Do take note that `latest` is a rolling release, so you'll always get the latest changes.

##### Installing from a Specific Tag

Select this option if you want to install a pinned version of Anthias. You'll be prompted to enter
a specific tag to install. You can find the tags in the
[releases](https://github.com/Screenly/Anthias/releases) page.

The script will check if the tag specified is valid and can be installed.
If it's not, you need to run the script again and enter a valid tag.

#### Prompt: Full System Upgrade

If you've selected **Yes** when prompted for an upgrade &ndash; i.e., "Would you like to perform a full system upgrade as well?"
&ndash; you'll get the following message when the installer is almost done executing:

```
Please reboot and run `/home/$USER/screenly/bin/upgrade_containers.sh` to complete the installation.

Would you like to reboot now?
```

You have the option to reboot now or later. On the next boot, make sure to run
`upgrade_containers.sh`, as mentioned above.

Otherwise, if you've selected **No** for the system upgrade, then you don't need to do a reboot for the containers to be started. However, it's still recommended to do a reboot.

### Installing with Balena

Go through the steps in [this documentation](/docs/balena-fleet-deployment.md)
to deploy Anthias on your own Balena fleet.

## :up: Migrating assets from Anthias to Screenly

This feature is only available in devices running Raspberry Pi OS at the moment.

### Migrating from a device running Raspberry Pi OS (Lite)

To get started, SSH to your Raspberry Pi running Anthias. For instance:

```bash
$ ssh pi@raspberrypi
```

Go to the project root directory and create a Python virtual environment, if you haven't created one.

```bash
$ cd ~/screenly
$ python -m venv venv/
```

Activate the virtual environment. You need to do this everytime right before you run the script.

```bash
$ source ./venv/bin/activate
```

Install the dependencies required by the assets migration script.

```bash
$ pip install -r requirements/requirements.local.txt
```

Before running the script, you should prepare the following:
* Your Screenly API key
* Anthias username and password, if your device has basic authentication enabled

Run the assets migration script. Follow through the instructions & prompts carefully.

```bash
$ python tools/migrate-assets-to-screenly.py
```

## :lady_beetle: Issues and bugs

> [!NOTE]
> We are still in the process of knocking out some bugs. You can track the known issues [here](https://github.com/Screenly/Anthias/issues). You can also check the discussions in the [Anthias forums](https://forums.screenly.io).

## :pushpin: Quick links

 * [Forum](https://forums.screenly.io/)
 * [Website](https://anthias.screenly.io) (hosted on GitHub and the source is available [here](https://github.com/Screenly/Anthias/tree/master/website))
 * [General documentation](https://github.com/Screenly/Anthias/blob/master/docs/README.md)
 * [Developer documentation](https://github.com/Screenly/Anthias/blob/master/docs/developer-documentation.md)
