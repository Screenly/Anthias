---
title: "piSignage Alternative"
description: "Anthias is free digital signage for every screen you own, with no player licenses, no account and no database to run. A straight comparison with piSignage for Raspberry Pi."
---

Anthias is free digital signage for **every** screen you own. piSignage is free
for two.

That is the difference in one line, and everything below is the detail behind
it. Both run on a Raspberry Pi and both will get a sign on the wall. Where they
part company is what happens when you add the third screen, what you have to
run yourself to avoid paying for it, and how much of the software you can
actually see.

## Anthias vs piSignage at a glance

| | Anthias | piSignage |
|---|---|---|
| Screens included free | **unlimited** | 2 |
| Cost beyond that | **none, ever** | annual subscription per player |
| Account required | **no** | yes, for the hosted dashboard |
| To self-host instead | nothing extra, it is the default | Node.js, MongoDB, FFmpeg, ImageMagick |
| Player source code | **published, GPLv2** | not published |
| Server source code | published, GPLv2 | published, MIT |
| In Raspberry Pi Imager | **yes, Pi 2 to Pi 5** | not listed |
| Works with no internet | **yes, dashboard included** | hosted dashboard needs the cloud |
| Updates | **atomic over the air, with rollback** | in-place upgrade scripts, reflash for a new OS |
| Hardware | Raspberry Pi 2 to 5, 64-bit x86, ARM boards via Armbian | Raspberry Pi and other Linux boards |

piSignage figures are its published list prices as of August 2026: two free
players, then $35 per player for a new player's first year and $20 per player
per year to renew. Check piSignage's own pricing page for current numbers.

## What it costs over a year

Both are free for one or two screens. Past that, one of them starts invoicing.
Crediting piSignage's two free players and using its list prices:

| Screens | Anthias | piSignage, first year | piSignage, renewing |
|---|---|---|---|
| 2 | $0 | $0 | $0 |
| 5 | **$0** | $105 | $60 |
| 10 | **$0** | $280 | $160 |
| 25 | **$0** | $805 | $460 |
| 50 | **$0** | $1,680 | $960 |

Anthias never charges per screen, so there is no renewal date, no license
count to reconcile, and no year two.

## The free route is not the same free route

piSignage does publish an open-source server, so in principle you can avoid the
subscription. In practice that means standing up and maintaining Node.js,
MongoDB, FFmpeg and ImageMagick, then keeping that server patched for as long
as your screens are on the wall. You have swapped a subscription for a database
to administer.

Anthias has no such fork in the road. Self-hosted **is** the product. Open
[Raspberry Pi Imager](https://www.raspberrypi.com/software/), pick Anthias from
the Digital signage and kiosks list, and flash. The device boots into its own
dashboard on your network with no server to build, no database, no account and
nothing to renew. The free path is the default path, and it starts from the
official Raspberry Pi tool rather than a PDF.

## Licensing you can rely on

Anthias is released under [GPLv2](https://github.com/Screenly/Anthias/blob/master/LICENSE),
with a commercial license available if copyleft does not suit your deployment.
Whichever you choose, your rights are written down.

piSignage publishes its **server** under MIT, and that is a real open-source
project. The **player**, which is the part that actually runs on your Pi, is a
different matter. As of August 2026 we could not find its source published
anywhere: the repository that carries the piSignage name holds documentation,
release notes, install guides, example widgets and translation files, with no
player application in it and no license file.

That distinction matters more than a license line. If the software driving your
screens is not published, you cannot audit what it does, you cannot fix it
yourself, and you cannot keep it alive if the vendor moves on. Every part of
Anthias, the player included, is in one public repository under GPLv2.

## Choose Anthias if

- You expect to run more than two screens and would rather spend the money on
  hardware than on licenses.
- You do not want an account, a cloud dependency, or a MongoDB instance in your
  signage stack.
- You want the dashboard to keep working when the internet does not, because it
  runs on your own network.
- You want to read the code that runs on your screens, not just the code that
  manages them.
- You show YouTube content. Paste a link and Anthias downloads the video to
  play locally, so the screen never buffers and never shows ads or recommended
  videos.
- You want to be running in about fifteen minutes from a flashed card.

## Bringing your content with you

Anthias imports directly from piSignage, so switching is not a retyping
exercise.

1. In Anthias, open **Settings** and choose **Import from piSignage** under
   *Import content*.
2. Enter `subdomain:email:password`, where the subdomain is the `<name>` in
   `<name>.pisignage.com`.
3. Review the media it finds and import it.

Your images, videos and web pages are copied onto the player and added to the
schedule, with per-item progress and a retry for anything that fails. Existing
assets are untouched, and re-running the import skips whatever already came
across. Your credentials are used to talk to the API during the import and are
not stored on the device.

Audio and documents such as PDF and PowerPoint are skipped, because Anthias
does not play them, as is any widget whose content is generated inside
piSignage. The wizard tells you what it skipped and why. Full detail is in the
[import documentation](/docs/import-content/).

## Updates that cannot half-brick a screen

Anthias updates over the air atomically. It runs on balenaOS, which keeps
two system partitions: an update is written to the spare one and the device
only switches over once the whole image is in place, rolling back on its own
if something goes wrong. A screen on a wall in another building moves to a new
version, base OS included, without a visit and without a half-applied update
leaving it dark.

piSignage updates in place. Its player image carries a single root partition
and a set of upgrade shell scripts that run against the live system, and a
larger jump means downloading a fresh image and running the installer again.
There is no second partition to fall back to, so an update that fails partway
leaves the device in whatever state it stopped at. Moving the base operating
system forward means reflashing the card by hand.

This is not a hypothetical difference. It is why a signage OS that cannot push
a full-image update over the air tends to leave older hardware on whatever it
originally shipped with, sometimes years later, while an atomic-OTA device
keeps current on its own.

## Try it on a spare Pi

No account, no card, no trial clock. Put Anthias on a spare Raspberry Pi next
to your current setup and judge it on the wall rather than on a comparison
table. Start with [Get Started](/get-started/), or read
[Raspberry Pi digital signage](/raspberry-pi-digital-signage/) and
[free digital signage software](/free-digital-signage-software/).

## Frequently asked questions

### Is Anthias cheaper than piSignage?

At one or two screens both are free. From the third screen on, Anthias is
cheaper by the whole invoice: it has no player license at any count, while
piSignage charges an annual subscription per additional player. Over ten screens
that is nothing against $160 to $280 a year, every year.

### Can I import my content from piSignage?

Yes, with a built-in wizard. Enter `subdomain:email:password` and Anthias copies
your images, videos and web pages across using the piSignage API, then adds them
to the schedule.

### Do I need an account or a server?

Neither. Anthias runs entirely on the device, so there is nothing to sign up for
and no back end to stand up. That is also why the dashboard keeps working
during an internet outage.

### Is piSignage open source too?

Partly. Its server is published under MIT and is genuinely open source. We could
not find published source for its player, the component that runs on the Pi: as
of August 2026 the repository bearing the piSignage name contains documentation,
release notes, install guides, examples and translations rather than player
code, and carries no license.

Anthias publishes everything, player included, in one repository under GPLv2,
with a commercial license available as an alternative.

### How do updates work?

Anthias updates over the air through balenaOS, which writes a new system image
to a spare partition and switches over only when it is complete, rolling back
automatically if it fails. That covers the base OS as well as the app.
piSignage updates its player in place with shell scripts, and a base-OS move
means reflashing the card.

### How do I manage several Anthias screens?

Each device runs its own dashboard, and two features keep that from being
repetitive: deploy and update a whole fleet over the air through
[Balena](/docs/balena-fleet-deployment/), and use the backup file to configure
one device then restore that setup onto the rest. What Anthias does not offer is
one dashboard that changes every screen's playlist at once.

### Does Anthias run on the same hardware?

Yes, and more of it. Raspberry Pi 2 through Pi 5, 64-bit x86 PCs such as an
Intel NUC or a retired desktop, and generic 64-bit ARM single-board computers
via Armbian on a best-effort basis.
