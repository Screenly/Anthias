# Installing on x86

Anthias supports installing on x86 devices running Debian 13 (Trixie) or Debian 12 (Bookworm) via the
installation script, which means that pre-built BalenaOS disk images are not yet available.
To make sure that the script will work, you need to install Debian in a specific way.

## Preparing the Disk Image

You can download the disk image from the [official Debian website](https://www.debian.org/download).
The file name should look something like `debian-13.x.x-amd64-netinst.iso`.

## Flashing the Disk Image to a USB Drive

You can use [Balena Etcher](https://www.balena.io/etcher/) or Raspberry Pi Imager (via the `Use custom` option)
to flash the disk image to a USB drive.

## Installing Debian

* Make sure that the USB drive is plugged into the x86 device.
* Make sure that the boot order is set to prioritize the USB drive.
* Boot up the x86 device.
* Follow the on-screen instructions to install Debian, while making sure to select the following options:
  * Do not set the root password so that the the non-root user will have `sudo` privileges.
  * Use the entire disk.
  * For **Software selection**, only leave the **SSH server** and **standard system utilities** selected.
    Deselect everything else.
* The system will reboot after the installation is complete. Make sure to remove the installation media (USB drive)
  before the system reboots.

## Preparing for Installation

* Make sure that you have `curl` installed. If not, you can install it by running:

  ```bash
  $ sudo apt update
  $ sudo apt install -y curl
  ```

* Disable password when running `sudo`:

  ```bash
  $ sudo visudo
  ```

  Add the following line to the end of the file:

  ```
  <username> ALL=(ALL) NOPASSWD: ALL
  ```

  Replace `<username>` with your username.
  Save and exit the file.

## References

* [The Official Debian Installation Guide](https://www.debian.org/releases/trixie/amd64)
