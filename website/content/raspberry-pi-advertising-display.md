---
title: "Raspberry Pi Advertising Display"
description: "Build a Raspberry Pi advertising display for free with Anthias. Loop promotions, offers, and video ads on any screen, with day-parting, portrait mode, and out-of-hours screen shutoff. No subscriptions or per-screen fees."
---

Anthias turns a Raspberry Pi into an **advertising display** for any screen, for free. Connect a Pi to a TV or monitor, install Anthias, and loop your promotions, offers, and video ads from a simple web dashboard. It is open source, self-hosted, and has no subscriptions or per-screen fees.

## A low-cost way to run in-store ads

Commercial advertising players are expensive and usually lock you into a monthly plan per screen. A Raspberry Pi running Anthias does the same job for the price of the hardware. That makes it a practical choice for small retailers, cafes, gyms, waiting rooms, and events that want eye-catching screens without an ongoing bill.

- **No per-screen fees.** Run one screen or a hundred at no software cost.
- **Full-screen playback.** Images and video loop automatically, all day.
- **Scheduling built in.** Give each ad a start and end date so campaigns rotate on their own.

## Choosing the hardware

| Part | What to get |
| ---- | ----------- |
| Raspberry Pi | Pi 4 or Pi 5 if your ads include video. Pi 2 and Pi 3 are fine for still images and light loops. |
| Storage | A reputable microSD card. On a Pi 5, an [SSD](/docs/pi5-ssd/) is worth it for a screen that will run for years. |
| Screen | Any display with an HDMI input. A commercial panel is built for long hours, but an ordinary TV works. |
| Power | The official supply for your Pi model. |

Resolution is worth a moment's thought. A Pi 5 drives 4K, and a Pi 4 will happily send 1080p content to a 4K panel, but genuine 4K *source* video on a Pi 4 will drop frames. For advertising loops, 1080p content is almost always the right call: it looks sharp on the wall and encodes small.

Anthias also runs on x86 mini PCs and ARM single-board computers if a Pi is not your preference, though a Pi 4 or Pi 5 remains the easiest route for video.

## Putting your ads on screen

Upload your images and videos, or point the display at a web page, and set how long each one shows. Anthias plays the active playlist on a loop and stores uploaded media locally, so the screen keeps running through a network outage. That matters in a shop, where a blank screen looks worse than a slightly stale one.

Most files just work. Photos from a phone, screenshots, and ordinary video clips are accepted, and anything in an awkward format is converted in the background before it plays.

You can mix promotions with useful ambient content so the screen stays fresh. Browse the [free signage apps](https://signage-apps.com/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-ads) that drop straight into Anthias, such as [weather](https://signage-apps.com/weather/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-ads), news feeds, and a [countdown timer](https://signage-apps.com/timer/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-ads) for a sale.

## Day-parting: the right ad at the right hour

An advertising screen earns more when the content matches the moment. Breakfast offers before eleven, lunch specials at midday, happy hour from five. Anthias handles this per asset, so you load a campaign once and let it run.

Each ad can be restricted by date range, by day of the week, and by a daily time window:

| Goal | Days | From | To |
| ---- | ---- | ---- | -- |
| Weekday business hours | Mon to Fri | 09:00 | 17:00 |
| Lunchtime menu | every day | 11:30 | 14:00 |
| Weekend evenings | Sat and Sun | 18:00 | 23:00 |
| Friday late-night promo | Fri | 22:00 | 02:00 |
| Single-day sale takeover | date range of one day | anytime | anytime |

Windows that cross midnight work as you would expect: a Friday window running 22:00 to 02:00 keeps playing into Saturday morning. Times use the device's own timezone, and the screen picks up a window opening or closing within about a minute. Full detail is in the [asset scheduling documentation](/docs/asset-scheduling/), and the [v2 API](/api/) can set these fields programmatically if you would rather drive campaigns from a script.

## Portrait orientation

Retail ad creative is often portrait. Set this under **Settings** in the dashboard, using the **Screen rotation** option, and it applies whichever Pi you are on. Many displays also offer a rotation setting in their own on-screen menu as a fallback. The [FAQ](/faq/) covers the boot-config route if you need it.

## Switching the screen off after hours

A shop window screen does not need to run at 3am. Under **Settings** there is a **Display schedule** that turns the screen off outside opening hours and back on in the morning, with per-weekday selection. It is off by default.

On a TV that supports HDMI-CEC the panel genuinely powers down, which saves electricity and adds years to its life. Most desktop monitors do not answer CEC, so on those the screen goes black instead. Either way it is one setting, not a timer plug. See the [display schedule documentation](/docs/display-schedule/).

## Making ads that actually read on a wall

The screen is usually seen in passing, from several metres away, by someone who did not intend to look at it. A few habits help:

- **One idea per slide.** A price, an offer, a name. Anything needing a second read is wasted.
- **Big type and strong contrast.** If it is not legible from across the room, it is not legible.
- **Design at the screen's resolution** and in its orientation, so nothing is scaled or letterboxed.
- **Keep video short.** Ten to fifteen seconds keeps the loop moving and the file small.
- **Give stills a few seconds each.** Long enough to read, short enough that a passer-by sees more than one.
- **Put the offer in the safe area.** Some TVs still overscan slightly, so avoid the outer edge.

## Running more than one screen

Anthias manages one screen per device, each with its own dashboard. Several screens showing the same loop means several Pis, set up individually. For a window bank or a handful of sites that is entirely workable, and the [import tools](/docs/import-content/) help you move a content set in rather than rebuilding it by hand. It is not the right shape for a large estate managed from one place, and it is better to know that up front.

## Getting started

Flash a Raspberry Pi with a pre-built Anthias image using Raspberry Pi Imager, run the one-line installer on an existing system, or deploy from balenaHub. See [Get Started](/get-started/) for the full walkthrough, or the [installation options](/docs/install/) for every route. For the wider picture, see [Raspberry Pi digital signage](/raspberry-pi-digital-signage/) and [free digital signage software](/free-digital-signage-software/).

## Frequently asked questions

### Can a Raspberry Pi really run an advertising screen?

Yes. A Raspberry Pi 4 or Pi 5 comfortably loops full-screen images and video on a single display, which covers the vast majority of in-store advertising needs.

### Is there any monthly cost?

No. Anthias is free and open source, so there are no software subscriptions or per-screen fees. You only pay for the Pi and the screen.

### Can I schedule ads for specific times of day?

Yes. Each ad takes a date range, a set of weekdays, and a daily time window, so a lunch offer or a Friday-night promo can be loaded ahead and left to run. Windows may cross midnight.

### Can the display run in portrait?

Yes. Set **Screen rotation** in the dashboard's Settings page. It works across the supported Pi models, and most displays also expose a rotation option in their own menu.

### Will the ads keep playing if the internet drops?

Uploaded images and videos play from the Pi's local storage, so the loop continues. Only content pulled from a live web page needs a working connection.

### Can a Raspberry Pi advertising display run 24/7?

Yes, and that is the normal way people use it. Use a good power supply and a reputable card, or an SSD on a Pi 5 for a multi-year install. If the screen does not need to be lit overnight, the display schedule can switch it off and back on.

### What resolution should my ads be?

1080p suits nearly every advertising loop and plays smoothly on a Pi 4 or Pi 5. A Pi 5 can drive a 4K panel, but 4K source video is more than a Pi 4 can decode without dropping frames.
