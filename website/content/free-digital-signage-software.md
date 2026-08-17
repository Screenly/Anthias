---
title: "Free Digital Signage Software"
description: "Anthias is free digital signage software for Raspberry Pi, x86 PCs and ARM single-board computers. Open source, self-hosted, with no subscriptions, no per-screen fees and no trial period."
---

Anthias is **free digital signage software**. Not a trial, not a one-screen teaser plan, and not a free tier designed to run out. It is open source under GPLv2, it runs on hardware you already own, and there is no paid version of it to upgrade to.

## What "free" means here

Most digital signage freeware turns out to be a limited edition of a paid product. Anthias is the whole thing:

- **No subscription.** No monthly bill, ever, at any number of screens.
- **No per-screen licence.** Run one display or fifty at no software cost.
- **No account.** Nothing to sign up for, no email address, no card.
- **No time limit or watermark.** Nothing expires and nothing is stamped on your content.
- **Open source.** Read, modify and redistribute it under [GPLv2](https://github.com/Screenly/Anthias/blob/master/LICENSE).

## Runs on hardware you already have

You are not buying into a proprietary player. Anthias runs on:

- **Raspberry Pi 2 through Pi 5.** A Pi 4 or Pi 5 is the recommendation for anything new.
- **64-bit x86 PCs**, including an Intel NUC, a mini PC, or an old desktop or laptop you have retired.
- **Generic 64-bit ARM single-board computers** via [Armbian](https://www.armbian.com/), such as Rock Pi, Orange Pi and Banana Pi boards, on a best-effort basis.

Add a screen with an HDMI input and that is the whole bill of materials. Your only costs are the hardware and the electricity.

## What you get

Upload an image or video, or point a screen at any web page, then set how long each item shows and give it a start and end date. Anthias loops the active playlist automatically and plays uploaded media from local storage, so the screen keeps running if the network drops.

Paste a YouTube link and the video is downloaded to play locally, without buffering and without ads or recommended videos appearing over your content. Scheduling goes down to days of the week and a daily time window, uploads never interrupt what is on screen, and a single backup file holds your settings and asset list so you can restore it later or move it to a new device.

There is also a [REST API](/api/) if you would rather drive content from a script than by hand, and a library of [free signage apps](https://signage-apps.com/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=free-software) that drop straight in, including [weather](https://signage-apps.com/weather/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=free-software), news feeds and a [world clock](https://signage-apps.com/world-clock/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=free-software).

## The honest limits

Free is only useful if you know the trade. Anthias manages **content per device**, and each install has its own dashboard, so there is no single screen where you set tomorrow's playlist for ten displays at once. You host it yourself, and support comes from the [community forum](https://forums.screenly.io) and [GitHub](https://github.com/Screenly/Anthias/issues) rather than a helpdesk with an SLA.

Setting up several devices is less repetitive than that makes it sound. A backup file holds your settings and asset list so you can restore it onto the next device, a fleet can be deployed and updated over the air through [Balena](/docs/balena-fleet-deployment/), and the [built-in importer](/docs/import-content/) brings a library across from Yodeck, ScreenCloud, piSignage or Xibo.

For one screen, or a handful you are content to manage individually, none of that is a problem and nothing is held back.

## Install it

Flash a disk image, run the one-line installer on an existing Linux system, or deploy from balenaHub. See [Get Started](/get-started/) for every option, the [feature list](/features/) for what it does, or [Raspberry Pi digital signage](/raspberry-pi-digital-signage/) if a Pi is your hardware.

## Frequently asked questions

### Is Anthias really free, or is there a paid tier?

Anthias is free, with no screen limit, no paid edition and nothing time-limited. It is released under [GPLv2](https://github.com/Screenly/Anthias/blob/master/LICENSE), so you can download, run, modify and redistribute it.

### What hardware does free digital signage software run on?

Raspberry Pi 2 through Pi 5, 64-bit x86 PCs such as an Intel NUC or a retired desktop, and generic 64-bit ARM single-board computers via Armbian on a best-effort basis. You also need a screen with an HDMI input.

### Can I use free digital signage software commercially?

Yes. Anthias is dual-licensed under GPLv2 and a commercial license, so you can run it in shops, cafes, lobbies and offices. Comply with the copyleft terms if you redistribute a modified version.

### Is free digital signage software good enough for real use?

For a single screen driven by one device, yes. Anthias has been developed in the open since 2012 and runs in schools, workshops, offices and storefronts.

### How does this compare to a commercial free plan?

Commercial platforms often give you one screen free and charge per screen after that. Anthias never charges per screen, but you host and maintain it yourself. See [Anthias vs Yodeck](/yodeck-alternative/) for a worked comparison.
