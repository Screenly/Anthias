---
title: "Raspberry Pi Info Screen"
description: "Build a Raspberry Pi info screen for free with Anthias. Show dashboards, calendars, notices and live data on any display. Open source and self-hosted, with no subscriptions or per-screen fees."
---

Anthias is the free, open-source way to build a **Raspberry Pi info screen**. Connect a Pi to any display, install Anthias, and show live information, notices, and dashboards that update on their own. It is self-hosted, with no subscriptions and no cloud account to sign up for.

## One Pi, an always-on information display

An info screen is any display that keeps people informed: a reception board, a team dashboard, a community notice board, a status wall. A Raspberry Pi is the perfect little engine for it because it is cheap, silent, and happy to run around the clock. Anthias handles the scheduling and playback so the screen looks after itself.

- **Live, self-updating content.** Point the screen at a web page or a dashboard and it refreshes on its own.
- **Mix sources.** Rotate images, video, and web pages in one playlist.
- **Set and forget.** A Pi behind the display can run unattended for months.

A Pi 4 or Pi 5 is the recommendation for a new build. Anthias also runs on Pi 2 and Pi 3, on x86 mini PCs, and on 64-bit ARM single-board computers via Armbian, so an old desktop or a spare board works just as well.

## What to show on an info screen

Upload images or video, or point Anthias at any web page, and set how long each item stays up. For ready-made content, the [free signage apps](https://signage-apps.com/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info) drop straight in as a URL. The ones that earn their place on an information display:

| Purpose | App |
| ------- | --- |
| Time on the wall | [digital clock](https://signage-apps.com/clock/digital/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info), [world clock](https://signage-apps.com/world-clock/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info) for distributed teams |
| Conditions | [weather](https://signage-apps.com/weather/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info), [air quality](https://signage-apps.com/air-quality/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info) |
| Reception and front of house | [opening hours](https://signage-apps.com/opening-hours/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info) |
| Team and culture | [birthdays](https://signage-apps.com/birthday/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info), [team milestones](https://signage-apps.com/team-milestone/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info) |
| Something to read | [news and RSS feeds](https://signage-apps.com/rss/bbc-top/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info), [Hacker News](https://signage-apps.com/rss/hacker-news/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info), [quotes](https://signage-apps.com/quotes/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info) |
| Ambient interest | [NASA image of the day](https://signage-apps.com/rss/nasa-iotd/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info), [on this day](https://signage-apps.com/on-this-day/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info) |
| Counting down | [timer](https://signage-apps.com/timer/?utm_source=anthias.screenly.io&utm_medium=referral&utm_campaign=pi-info) |

Each one is a link you paste into Anthias, so a single Pi can rotate through a whole set of them.

## Showing a calendar on the screen

A shared calendar is one of the most useful things you can put on a wall: what is in the meeting room today, who is on shift, what is on this week.

Anthias shows a calendar the same way it shows anything on the web, as a web page asset. In Google Calendar, open the settings for the calendar you want, find the integration section, and copy the public embed URL. Paste that into Anthias as a web page and set a duration. The screen keeps it up to date on its own.

The one thing to plan for: a Pi on the wall has nobody to log in as. A calendar that is private to your account will show a sign-in page rather than your events. Either publish the calendar so the embed URL works without an account, or use a dedicated calendar for the screen with only the entries you are happy to display. The same rule applies to any tool you put on a screen.

## Live dashboards

The same approach covers dashboards. Anything that renders in a browser can go on the screen: a Grafana board, a sales or support dashboard, a build monitor, an internal status page.

Two practical notes. First, authentication again: use whatever your tool offers for unattended viewing, such as a public dashboard link, a kiosk or read-only token, or an internal page that does not require a session. Second, design for the distance. A dashboard built for a laptop is usually unreadable from across a room, so cut the number of panels and pick a layout with large figures.

## A digital notice board

For a school corridor, a community centre, a staffroom or a block of flats, an info screen replaces the pinboard nobody updates. The advantage over paper is that notices retire on their own.

Every asset takes a start and end date, so a term-time notice, a scheduled maintenance warning or an event poster can be loaded in advance and will disappear when it is no longer true. Assets can also be limited to particular weekdays and a daily time window, which is handy when a morning notice should not still be up at closing time. The [asset scheduling documentation](/docs/asset-scheduling/) has the full detail, and the [v2 API](/api/) can set those fields from a script if notices come out of another system.

## A Pi that boots straight into a full-screen page

If what you want is a screen that comes up showing one web page, with no desktop, no browser chrome, no mouse pointer and no login prompt, that is what Anthias gives you out of the box. You do not need to configure a window manager, hide a cursor, or write an autostart script. Flash the image, add your URL, and the Pi comes back to the same screen after a power cut.

## Getting the picture right

- **Resolution** follows whatever the operating system reports, so there is no setting in the dashboard. On Raspberry Pi OS use `sudo raspi-config`, then **Display Options** and **Resolution**, or set `hdmi_group` and `hdmi_mode` in `/boot/firmware/config.txt`.
- **Portrait** displays are common for notice boards. Set **Screen rotation** in the dashboard's Settings page.
- **Out of hours**, the **Display schedule** setting under Settings can switch the screen off overnight and back on in the morning, per weekday. On a TV that supports HDMI-CEC the panel genuinely powers down; most desktop monitors simply go black. See the [display schedule documentation](/docs/display-schedule/).

## Leaving it alone for months

Uploaded images and video play from the Pi's local storage, so a network outage does not blank the screen. Only live web content needs a working connection, and a dashboard or calendar will pick up again when the link returns.

For an install that should last years, use a reputable microSD card and the official power supply, or move a Pi 5 onto an [SSD](/docs/pi5-ssd/) to avoid card wear. When you add a file, the dashboard shows a **Processing** badge while it is prepared and a **Failed** badge with a short explanation if something is wrong, so problems surface in the interface rather than in a log file.

Anthias can also tell you whether the connected TV is actually powered on, which is the difference between spotting a dark screen from your desk and finding out when someone complains. The dashboard shows system information at a glance and flags when an update is available, and the device can be restarted or shut down remotely. If the screen sits somewhere public, the interface can be put behind a password and served over HTTPS.

## More than one screen

Anthias manages content per device, each with its own dashboard, so two or three info screens means two or three Pis. A few things take the sting out of that: a backup file captures your settings and asset list so you can restore it onto the next device rather than rebuilding it, and a fleet can be deployed and updated over the air through [Balena](/docs/balena-fleet-deployment/).

What you do not get is one dashboard that changes the content on every screen at once. For a building or a floor that is fine. For a large estate it is the wrong tool, and it is better to say so now than after you have bought the hardware.

## Getting started

Install Anthias from a pre-built disk image or the one-line installer, connect your Pi to the screen, and add your content. See [Get Started](/get-started/) for the full walkthrough, the [installation options](/docs/install/) for every route, or the broader guide to [Raspberry Pi digital signage](/raspberry-pi-digital-signage/). If cost is what brought you here, [free digital signage software](/free-digital-signage-software/) covers what free does and does not include.

## Frequently asked questions

### What can a Raspberry Pi info screen display?

Anything you can show as an image, a video, or a web page: dashboards, calendars, notices, live data, and the ready-made signage apps above.

### Is it free?

Yes. Anthias is open source and free to run on any number of Raspberry Pi devices. You only need the hardware.

### Can I show a Google Calendar on a Raspberry Pi?

Yes. Copy the calendar's public embed URL from its settings and add it to Anthias as a web page asset. The calendar needs to be viewable without signing in, because the Pi has no account to log in with, so either publish it or keep a separate calendar for the screen.

### Can I put a Grafana or business dashboard on the screen?

Yes, if the dashboard can be viewed without an interactive login. Use a public link or a read-only kiosk token, and simplify the layout so the numbers are legible from a distance.

### Do notices expire on their own?

Yes. Every asset has a start and end date, and can also be limited to specific weekdays and a daily time window, so notices appear and retire without anyone remembering to take them down.

### Will the screen come back on its own after a power cut?

Yes. The Pi boots straight back into the playlist with no desktop and no login step, which is the main reason this setup suits screens in places nobody visits daily.

### Can the display run in portrait?

Yes. Use the **Screen rotation** option in the dashboard's Settings page. Many displays also offer a rotation setting in their own on-screen menu.

### Does it need to stay online?

No. Uploaded images and videos play from local storage, so the loop keeps running offline. Only live content such as a calendar, a feed or a dashboard needs a connection to refresh.
