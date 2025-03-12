# How to install on a Raspberry Pi 5 with PCI-e SSD

## Hardware

The following guide has been tested using a Raspberry Pi 5 with 8GB RAM and a [GeeekPi P33 PoE+PCI-e HAT](https://pipci.jeffgeerling.com/hats/geeekpi-p33-m2-nvme-poe-hat.html).

Other HAT's should work just fine as long as they are supported by the Pi.

We used a 256GB M.2 NVMe SSD as it came in the 2242 form factor, and was small and cheap (at time of writing, these were approx £15+vat from our supplier).

You may also need a microSD card to initially configure your Pi. Once the first steps are done you won't require this.

## Installation

Using the Raspberry Pi Imager and appropriate USB adapters, write the 64-bit version of Raspberry Pi OS to both a SD card and the SSD.

There are other ways (Such as a network boot if enabled on the Pi, Booting from SD and using the SD Card copier utility to copy the OS to the SSD, or Booting from SD and using the RPI Imager to write a fresh copy to the SSD, or using rpiboot mode to present the SSD as mass storage to a PC.

Do whatever is easiest for you!

### Boot from SD

Depending on the bootloader version of your Pi, you may need to boot from SD first and set the bootloader to boot from PCIe.

- Once booted, run the RPI EEPROM configurator: `sudo rpi-eeprom-config -edit`
- Change the boot order to: `BOOT_ORDER=0xf416`
- Add the line: `PCIE_PROBE=1`
- Type: Ctrl-O to save the file
- Type: Ctrl-X to exit the editor
- Remove the SD card, and power cycle the Pi.

### Boot from SSD

Once your Pi is booting from the SSD, you have a couple of housekeeping tasks to perform;

- Update the OS using `sudo apt update` and `sudo apt full-upgrade -y`
- Check the bootloader is at the latest version using `sudo rpi-eeprom-update`
- If there is an EEPROM update, then use `sudo raspi-config` to update it
- Go to `6 Advanced Opitions` => `A5 Bootloader Version` => `E1 Latest`, then select `Yes`
- Reboot the Pi
- Finally, you'll need to run the Anthias installer: `bash <(curl -sL https://install-anthias.srly.io)`
- Follow the prompts to install and reboot the Pi
- Once the install has completed, don't forget to change the password for your Pi uinsg `passwd`

### Post install issues

If after it completes and you reboot you still get a black screen, simply press `control+alt+f1` to get into the console and then run: `./screenly/bin/upgrade_containers.sh` this should re-run the container creation step and have the system up and running properly.