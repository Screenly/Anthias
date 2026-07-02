---
title: "PC (x86) Installation"
description: "Install Anthias on PC / x86 hardware running Debian 13 (Trixie) or Debian 12 (Bookworm)."
slug: "pc"
aliases:
  - "/docs/x86-installation/"
---

Anthias runs on any 64-bit PC (something like an Intel NUC works well) once you've prepared a fresh Debian install. Pre-built BalenaOS images aren't available for PC hardware yet, so you'll install Debian manually and then run the standard Anthias installer on top of it.

> **Note**
>
> Anthias supports **64-bit Debian 13 (Trixie)** and **64-bit Debian 12 (Bookworm)** on PCs.

> **No desktop environment required (or wanted)**
>
> The host runs headless — no GNOME, no KDE, no Xorg, no display manager. The Anthias viewer container ships its own minimal Wayland compositor (`cage`, a wlroots-based kiosk compositor) which acquires DRM master directly from the kernel and renders straight to the HDMI output. A pre-installed desktop would compete with it for the display and break the boot-to-content experience.
>
> If you already installed Debian with a desktop, remove it (`sudo apt purge --auto-remove gnome\* xserver-xorg\* lightdm gdm3 sddm` and reboot) before continuing.

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
   * **Root password:** leave it blank. When you skip the root password, the Debian installer installs `sudo` and adds your regular user to the `sudo` group automatically. If you *set* a root password instead, Debian does **neither** — `sudo` won't even be installed — and you'll have extra work to do in Step 4. Leaving it blank is strongly recommended.
   * **Partitioning:** use the entire disk.
   * **Software selection:** check only **SSH server** and **standard system utilities**. **Uncheck every desktop environment** (GNOME, Xfce, KDE Plasma, …) — Anthias renders from inside a container and does not use any host-side graphical session.
4. When the installer finishes, remove the USB drive **before** the system reboots into the freshly installed Debian.

## Step 4 — Prepare the system for Anthias

Once you can SSH (or log in locally) to the new install:

> **If you set a root password in Step 3**
>
> Your user can't run `sudo` yet — in fact `sudo` isn't installed, so every command below will fail with `sudo: command not found`. Fix this once, as `root`, then continue:
>
> ```bash
> $ su -          # enter the root password you set
> # apt update && apt install -y sudo
> # /usr/sbin/usermod -aG sudo <username>   # replace <username>
> # exit
> ```
>
> Log out and back in (so your new group membership takes effect), then continue below. If you left the root password blank, skip this box — `sudo` already works.

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

You're now ready to run the standard installer. Follow the [scripted install steps](/docs/install/#installing-on-raspberry-pi-os-lite-or-debian) — they're the same on PC as on a Raspberry Pi.

## References

* [Official Debian Installation Guide (Trixie / amd64)](https://www.debian.org/releases/trixie/amd64)
