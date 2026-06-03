---
title: "Installation Options"
description: "Install Anthias on Raspberry Pi or Debian: imager, balenaHub, releases, or scripted install."
slug: "install"
aliases:
  - "/docs/installation-options/"
---

The quickest way to get started on a Raspberry Pi is to use [Raspberry Pi Imager](https://www.raspberrypi.com/software/), where you can find Anthias under **Other specific-purpose OS &rarr; Digital signage and kiosks**. Pick the entry that matches your Pi (Pi 2, Pi 3, Pi 4, or Pi 5), select your SD card, and flash &mdash; the device boots straight into Anthias.

![Raspberry Pi Imager showing Other specific-purpose OS category](/docs/images/imager-01.png)

![Raspberry Pi Imager listing Anthias as a selectable image](/docs/images/imager-02.png)

![Raspberry Pi Imager confirming the SD card target before writing Anthias](/docs/images/imager-03.png)

# Using the images from balenaHub

> **Important**
>
> This option is recommended for those who want to install Anthias without touching the
> command line interface. When a new rolling release is available, updates will automatically
> be installed on your device.

Balena made a [big update to their IoT marketplace](https://blog.balena.io/creating-an-iot-marketplace/). Included in that change is the launch of
[Fleets for Good](https://hub.balena.io/fleets-for-good). With that, you may find it hard to find the Anthias images on the marketplace. In the meantime,
here are the links to the images:

* [Raspberry Pi 5](https://hub.balena.io/fleets-for-good/2209774/anthias-pi5)
* [Raspberry Pi 4](https://hub.balena.io/fleets-for-good/1971389/anthias-pi4)
* [Raspberry Pi 3](https://hub.balena.io/fleets-for-good/1971388/anthias-pi3)
* [Raspberry Pi 2](https://hub.balena.io/fleets-for-good/1971385/anthias-pi2)

Go to one of the links above and click the *Join* button, then select either *Ethernet only* or *Wifi + Ethernet* for Network options.
You can either click the *Flash* button to open balenaEtcher (make sure that it's installed) or download the image file and flash it using your preferred imager.
Flash the SD card and boot up your Raspberry Pi. It will take a few minutes to boot up and start the services.

Alternatively, you can [download our pre-built Balena disk images from the releases](#using-the-images-from-the-releases).

# Using the images from the releases

You can find the latest release [here](https://github.com/Screenly/Anthias/releases/latest). From there, you can download the disk image that you need and flash it to your SD card.
The image file looks something like `<yyyy>-<mm>-<dd>-raspberry<version>.zst`. Take note that the `.img` file is compressed in this `.zst` file.

> **Note**
>
> We started to release the images in `.zst` format in [v0.20.0](https://github.com/Screenly/Anthias/releases/tag/v0.20.0) so that the images are smaller in size. Using `zip` with the `-9` flag won't make the each of the images smaller than 2 GB.
>
> Raspberry Pi Imager supports the `.zst` format from version [v1.9.4](https://github.com/raspberrypi/rpi-imager/releases/tag/v1.9.4) onwards. For those who are using [balenaEtcher](https://etcher.balena.io/), you can use the `zstd` command to decompress the image file first:
>
> ```
> zstd -d <yyyy>-<mm>-<dd>-raspberry<version>.zst
> ```

> **macOS: "Error writing to storage device"**
>
> Raspberry Pi Imager **2.0.2 through at least 2.0.7** has a macOS bug that aborts mid-write with *"Error writing to storage device. Some writes failed to complete."* It is triggered by writing any image that is decompressed on the fly &mdash; not just our `.zst` images, but also `.img.xz` and even uncompressed `.img` files (see rpi-imager [#1605](https://github.com/raspberrypi/rpi-imager/issues/1605) and [#1489](https://github.com/raspberrypi/rpi-imager/issues/1489)). The card itself is fine.
>
> The fix was merged upstream in [rpi-imager#1621](https://github.com/raspberrypi/rpi-imager/pull/1621) (May 2026), so the simplest solution is to **update Raspberry Pi Imager to a release newer than 2.0.7**. If you can't update, work around it by decompressing the image yourself and flashing the resulting `.img`:
>
> ```
> zstd -d <yyyy>-<mm>-<dd>-raspberry<version>.zst
> ```
>
> Then select the extracted `.img` in Raspberry Pi Imager (or [balenaEtcher](https://etcher.balena.io/)), which skips the on-the-fly decompression path that trips the bug.

Devices installed from a disk image join the balena fleet and track the latest stable release. The image ships preloaded with the release it was built from, so the device boots and runs fully offline out of the box, then receives later releases automatically over the air once it has connectivity.

# Installing on Raspberry Pi OS Lite or Debian

#### Overview

If you'd like more control over your digital signage instance, try installing it on Raspberry Pi OS Lite or Debian.

> **Important**
>
> Installing on a **PC (x86)**? Follow the [PC installation guide](/docs/pc/) first to prepare Debian — the script below assumes that setup.

> **Note**
>
> For Raspberry Pi 5:
> * We recommend using the 64-bit version of Raspberry Pi OS Lite (Trixie or Bookworm)
> * 32-bit Raspberry Pi OS is not supported on Pi 5
> * If you experience any issues, please report them either:
>   * On our [forums](https://forums.screenly.io)
>   * As a [GitHub issue](https://github.com/Screenly/Anthias/issues)
>   * In [GitHub Discussions](https://github.com/Screenly/Anthias/discussions)

The TL;DR for on [Raspberry Pi OS](https://www.raspberrypi.com/software/) or Debian is:

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

![Animated walkthrough of the bash <(curl ...) Anthias installer prompts](/docs/images/install-anthias.gif)

**This installation will take 15 minutes to several hours**, depending on variables such as:

* The Raspberry Pi hardware version
* The SD card
* The internet connection

> **Note**
>
> During ideal conditions (Raspberry Pi 3 Model B+, class 10 SD card and fast internet connection), the installation normally takes 15-30 minutes.

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
Please reboot and run `/home/$USER/anthias/bin/upgrade_containers.sh` to complete the installation.

Would you like to reboot now?
```

You have the option to reboot now or later. On the next boot, make sure to run
`upgrade_containers.sh`, as mentioned above.

Otherwise, if you've selected **No** for the system upgrade, then you don't need to do a reboot for the containers to be started. However, it's still recommended to do a reboot.

# Installing with Balena

Go through the steps in [this documentation](/docs/balena/)
to deploy Anthias on your own Balena fleet.

# Installing on a Raspberry Pi 5 with an SSD

Go through the steps in [this documentation](/docs/pi5-ssd/)
to deploy Anthias on a Pi5 with an SSD
