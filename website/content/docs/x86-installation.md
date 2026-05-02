---
title: "PC (x86) Installation"
description: "Install Anthias on PC / x86 hardware running Debian 13 (Trixie) or Debian 12 (Bookworm)."
---

Anthias runs on any 64-bit PC (something like an Intel NUC works well) once you've prepared a fresh Debian install. Pre-built BalenaOS images aren't available for PC hardware yet, so you'll install Debian manually and then run the standard Anthias installer on top of it.

> **Note**
>
> Anthias supports **64-bit Debian 13 (Trixie)** and **64-bit Debian 12 (Bookworm)** on PCs.

## What you'll need

* A 64-bit PC (most NUCs, mini-PCs, and old laptops work).
* A USB drive (4 GB or larger) to write the Debian installer to.
* A keyboard, monitor, and network cable for the PC during install.

## Step 1 — Download Debian

Download the **netinst** image for AMD64 from the [official Debian website](https://www.debian.org/download). The filename will look like:

```
debian-13.x.x-amd64-netinst.iso
```

## Step 2 — Write the installer to a USB drive

Flash the ISO to a USB drive using one of:

* [balenaEtcher](https://www.balena.io/etcher/) — recommended, cross-platform.
* Raspberry Pi Imager — pick **Use custom** and select the ISO.

## Step 3 — Install Debian

1. Plug the USB drive into the PC.
2. Set the boot order in BIOS/UEFI to boot from USB first.
3. Power on the PC and follow the Debian installer prompts. When you reach these screens, choose:
   * **Root password:** leave it blank. Skipping the root password makes your regular user a `sudo` user automatically.
   * **Partitioning:** use the entire disk.
   * **Software selection:** check only **SSH server** and **standard system utilities**. Uncheck everything else (no desktop environment).
4. When the installer finishes, remove the USB drive **before** the system reboots into the freshly installed Debian.

## Step 4 — Prepare the system for Anthias

Once you can SSH (or log in locally) to the new install:

1. Install `curl` if it isn't already there:

   ```bash
   $ sudo apt update
   $ sudo apt install -y curl
   ```

2. Allow your user to run `sudo` without entering a password — the Anthias installer expects this. Open the sudoers file:

   ```bash
   $ sudo visudo
   ```

   Add this line at the end (replace `<username>` with your actual username):

   ```
   <username> ALL=(ALL) NOPASSWD: ALL
   ```

   Save and exit the editor.

## Step 5 — Run the Anthias installer

You're now ready to run the standard installer. Follow the [scripted install steps](/docs/installation-options/#installing-on-raspberry-pi-os-lite-or-debian) — they're the same on PC as on a Raspberry Pi.

## References

* [Official Debian Installation Guide (Trixie / amd64)](https://www.debian.org/releases/trixie/amd64)
