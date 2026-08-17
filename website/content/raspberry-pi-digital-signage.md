---
title: "Raspberry Pi Digital Signage"
description: "Turn a Raspberry Pi into a digital sign for free with Anthias, the open-source digital signage platform. Photos, video, web pages, YouTube and live streams, scheduled from a simple dashboard. No subscriptions."
---

Anthias is the free, open-source way to run **digital signage on a Raspberry Pi**. Install it on a Pi you already own, connect the Pi to any screen over HDMI, and manage what plays from a simple web dashboard. There are no subscriptions and no per-screen fees, and your content stays on your own network.

## Why run digital signage on a Raspberry Pi?

A Raspberry Pi is small, quiet, cheap, and sips power, which makes it ideal for a screen that runs all day. With Anthias, one Pi behind a display becomes a self-contained sign that loops your content and keeps running unattended for months.

- **Free and open source.** Formerly Screenly OSE, developed in the open since 2012 and one of the most widely deployed signage projects in the world.
- **Self-hosted.** No cloud account required. You control the device and the content.
- **Runs on hardware you have.** Raspberry Pi and ordinary x86 PCs, plus 64-bit ARM single-board computers on a best-effort basis.

## Which Raspberry Pi to use

| Model | Notes |
| ----- | ----- |
| Raspberry Pi 5 | Recommended. Drives 4K, and can boot from an [SSD](/docs/pi5-ssd/) for a multi-year install. |
| Raspberry Pi 4 | Recommended. Comfortable with 1080p video, and will feed a 4K panel from 1080p content. |
| Raspberry Pi 3 B and 3 B+ | Supported, in maintenance mode. Fine for images and web pages. |
| Raspberry Pi 2 B | Supported, in maintenance mode. Best kept to still images and light loops. |
| x86 PC | 64-bit. A mini PC, an Intel NUC, or a retired desktop. |

Pi 4 or later is the recommendation for anything new. Add a screen with an HDMI input, a reputable microSD card and the official power supply, and that is the whole bill of materials.

## Getting it running

Flashing a card with [Raspberry Pi Imager](https://www.raspberrypi.com/software/) is the quickest route. Anthias is listed there directly, under **Other specific-purpose OS**, then **Digital signage and kiosks**, then **Anthias**. Pick the entry matching your board, write the card, and the device boots straight into Anthias.

On first boot the TV itself shows the device's network address and a QR code. Scan it with your phone and you land on the dashboard, so there is no hunting through your router for an IP address.

If you would rather install onto an existing Raspberry Pi OS Lite or Debian system, one command does it:

```bash
$ bash <(curl -sL https://install-anthias.srly.io)
```

Every route, including balenaHub fleets and the pre-built release images, is covered in [Get Started](/get-started/) and the [installation options](/docs/install/).

## What you can put on screen

Anything that goes on a screen, all in one playlist. Drag a file in or paste a link.

- **Photos and video.** Phone photos, screenshots and camera files upload and play without converting anything first. If a file is not quite right for your hardware, Anthias prepares it in the background while whatever is already on screen keeps playing.
- **Web pages and dashboards.** Point Anthias at any page, a status board, a weather widget, an internal report, and set an auto-refresh interval so it reloads on its own and stays current.
- **YouTube, without the YouTube.** Paste a link and the video is downloaded to play locally, so the screen never buffers and never shows ads or recommended videos.
- **Live streams and video feeds**, alongside everything else in the same playlist.
- **Full-HD output.** 1080p with hardware-accelerated video, and sound over HDMI or the headphone jack.

For ready-made content, browse the [free signage apps](https://signage-apps.com/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-hub) that work with Anthias out of the box: live [weather](https://signage-apps.com/weather/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-hub), a [world clock](https://signage-apps.com/world-clock/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-hub), news and RSS feeds, countdown timers, and more. Each one is just a link you paste into Anthias, so a single Pi can rotate through a whole set of them.

## Deciding when things play

Scheduling is per asset, so you load content once and let the playlist manage itself.

- **A date range.** An item appears for a campaign and disappears on its own when it is over.
- **Days of the week.** One playlist on weekdays, another at weekends, something special only on Fridays.
- **A time of day.** Opening-hours content from nine to five and something else overnight. Windows that cross midnight work too.
- **A duration per item.** Photos and web pages stay up as long as you tell them to; videos work out their own length.
- **Order or shuffle.** Drag items into place, or shuffle so each loop differs.
- **Skip and pause.** Jump forward or back, or switch an item off temporarily without deleting it.

The [asset scheduling documentation](/docs/asset-scheduling/) covers the details, including how overnight windows behave.

## Running it from your browser

The dashboard runs on the device itself, on your network. There is no app to install and no cloud account, and you can open it from a laptop, phone or tablet.

- **Preview before you publish.** Click any item to see exactly what the screen will show.
- **Know when your screen is dark.** Anthias can tell whether the connected TV is actually powered on, so you can spot a dead display without walking over.
- **System information at a glance.** Uptime, storage and memory in use, the software version, and the device's address.
- **Update notices.** The dashboard says when a newer version is out.
- **Remote restart and shutdown**, which matters once your screens are scattered around a building.
- **Screen rotation** for portrait installs, and a **display schedule** that switches the screen off outside opening hours. See the [display schedule documentation](/docs/display-schedule/).

If the screen is somewhere public, turn on username and password protection, and encrypt the dashboard over HTTPS with a self-signed certificate, a free auto-renewing one for your own domain, or your own certificate.

## Bringing content with you

Moving from another platform does not mean rebuilding your library. Anthias has a built-in import wizard for **Yodeck, ScreenCloud, piSignage and Xibo**: paste an API token, review what it finds, and import. See the [import documentation](/docs/import-content/), or the [Yodeck comparison](/yodeck-alternative/) if that is where you are coming from.

A single backup file holds your settings and asset list, so you can restore it later or move it onto a brand-new device rather than setting one up from scratch.

## More than one screen

Anthias manages content per device, each with its own dashboard. Several screens means several Pis, and there is no single dashboard where you change tomorrow's playlist for all of them at once.

Two things make a small estate practical: a fleet can be deployed and updated over the air through [Balena](/docs/balena-fleet-deployment/), and the backup file lets you clone a configured device onto the next one. For a shop, a building or a floor that works well. For a large multi-site estate managed centrally, it is the wrong tool.

## Automating it

The [REST API](/api/) covers uploads, playlist changes and the scheduling fields, so content can be pushed from your own scripts or another system. It is fully documented and keeps compatibility with older integrations, so an automation you write now will not break on the next release.

## Where to go next

- [Raspberry Pi advertising display](/raspberry-pi-advertising-display/) for promotions, offers and video ads in a shop or venue.
- [Raspberry Pi info screen](/raspberry-pi-info-screen/) for dashboards, calendars and notice boards.
- [Free digital signage software](/free-digital-signage-software/) for what free does and does not include.
- [The full feature list](/features/), or the [FAQ](/faq/).

## Frequently asked questions

### Is Raspberry Pi digital signage really free?

Yes. Anthias is open source and free to run on as many Raspberry Pis as you like. The only cost is the hardware, which you may already own.

### Which Raspberry Pi models are supported?

Pi 2 B, Pi 3 B, Pi 3 B+, Pi 4 and Pi 5, plus 64-bit x86 PCs. Pi 2 and Pi 3 are in maintenance mode, so a Pi 4 or Pi 5 is the recommendation for a new installation, particularly if you plan to play video.

### Do I need an internet connection?

You need one to install Anthias and to show web-based content, but the device runs on your own network and does not depend on any external cloud service to keep playing. Uploaded images and videos play from local storage, so the screen survives an outage.

### Can I play YouTube videos on a Raspberry Pi sign?

Yes. Paste the link and Anthias downloads the video to play locally, which means no buffering and no ads or recommended videos appearing over your content.

### Can the screen show a live web page?

Yes. Add the page as an asset and set an auto-refresh interval, so a dashboard, feed or internal report stays current on its own. Bear in mind that a screen has nobody to log in as, so use a page that is viewable without signing in.

### How do I set up a second screen?

Install Anthias on another device. To avoid repeating the configuration, download the backup file from the first one and restore it onto the new device, then adjust the playlist.

### Can I run it in portrait?

Yes. Use the **Screen rotation** option in the dashboard's Settings page. Many displays also offer a rotation setting in their own menu.
