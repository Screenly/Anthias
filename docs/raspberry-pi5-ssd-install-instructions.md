# How to install on a Raspberry Pi 5 with PCI-e SSD

## Hardware

The following guide has been tested using a Raspberry Pi 5 with 8GB RAM and a [GeeekPi P33 PoE+PCI-e HAT](https://pipci.jeffgeerling.com/hats/geeekpi-p33-m2-nvme-poe-hat.html).

Other HAT's should work just fine as long as they are supported by the Pi.

The author of this guide used a 256GB M.2 NVMe SSD as it came in the 2242 (22mm x 44mm) form factor. The original version of the P33 HAT only supported 2230 and 2242 SSDs, but a newer version extends further and supports 2260 and 2280 SSDs as well.

## Booting

Early Pi 5's do not support PCIe boot as part of the factory bootloader configuration. If you have a version of the bootloader prior to at least **Mon 23 Sep 13:02:56 UTC 2024 (1727096576)** then it is likely that you will need to follow the Boot from SD steps first.

## Installation

Using the Raspberry Pi Imager and appropriate USB adapters, write the 64-bit version of **Raspberry Pi OS Lite (Bookworm)** to the microSD card. Depending on your deployment preference, you can either write the same OS or you can deploy the **BalenaOS** image to the SSD.

There are a few alternative ways to install:
- Network boot (if enabled on the Pi)
- Booting from the microSD card and using the SD Card copier utility to copy the OS to the SSD
- Booting from the microSD and using the Raspberry Pi Imager to write a fresh copy to the SSD
- Using `rpiboot` mode to display the SSD as a mass storage device on a PC.

Do whatever is easiest for you!

### Boot from SD

Depending on the bootloader version of your Pi (Confirmed  that as of at least **Mon 23 Sep 13:02:56 UTC 2024 (1727096576)** you do not need to perform this step), you may need to boot from microSD first and set the bootloader to boot from PCIe.

> [!NOTE]
> You can check the bootloader version by using the command `sudo rpi-eeprom-update` which will tell you what version your Pi 5 is running.
> This command will also tell you if an update is available, which you can install with `sudo rpi-eeprom-update -a`.
> The author of this document would welcome feedback if once the above update is performed, wether you still need to run the command below. The author tested the below and found it successful, then updated the bootloader so YMMV!
> This looks a little bit like this;
> ![rpi-eeprom-update](/docs/images/rpi-eeprom-update.png)

- Once booted, run the RPI EEPROM configurator: `sudo rpi-eeprom-config -edit`. This will open up the [Nano](https://www.nano-editor.org/) text editor.
- Change the boot order to: `BOOT_ORDER=0xf614`
- Add the line: `PCIE_PROBE=1`
- Type **Ctrl-O** to save the file.
- Type **Ctrl-X** to exit the editor.
- Remove the microSD card, and power cycle the Pi.

### Boot from SSD

Once your Pi is booting from the SSD, if you installed the Raspberry Pi OS image you have a couple of housekeeping tasks to perform;

- Update the OS using `sudo apt update -y` and `sudo apt full-upgrade -y`
- Check the bootloader is at the latest version using `sudo rpi-eeprom-update`
- If there is an EEPROM update, then use `sudo raspi-config` to update it
- Go to `6 Advanced Opitions` &rarr; `A5 Bootloader Version` &rarr; `E1 Latest`, then select `Yes`
- Reboot the Pi.
- Finally, you'll need to run the Anthias installer: `bash <(curl -sL https://install-anthias.srly.io)`
- Follow the prompts to install and reboot the Pi
- Once the install has completed, don't forget to change the password for your Pi uinsg `passwd`

### Check your boot order

You can view your current boot order by running `rpi-eeprom-config` and checking the output.
Using [This document](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#BOOT_ORDER) you can view the boot options and configure your boot order to suit your requirements.

### Post Install Issues

> [!NOTE]
> If you still get a black screen after the installation completes and after a reboot, simply press `Ctrl-Alt-F1` to get into the console (or SSH in) and then run `./screenly/bin/upgrade_containers.sh`. this should re-run the container creation step and have the system up and running properly.
