---
title: "Multi-screen management"
description: "Run one Anthias device as a controller that publishes the same playlist to the other screens on your network: recommended hardware, setup, health monitoring, and security."
slug: "multi-screen"
---

One Anthias device can act as a **controller** for the other Anthias
screens on your local network. You build the playlist once on the
controller and publish it to every screen, and you can see which screens
are online, how healthy they are, and reboot or control them from one
page.

It is meant for small deployments on a single network: a lobby, a
shop, a classroom wing. Every screen shows the same playlist (mirror
mode), and everything stays on your network. If you need different
content on different screens, screens in several locations, or remote
management over the internet, [Screenly](https://www.screenly.io) is
built for that.

## Controller and screens

Both roles run the same Anthias software. You choose the role in
Settings:

* The **controller** is the device you manage everything from. It
  still plays its own playlist on its own display, and that playlist is
  what gets published. Turn on **Settings → Controller mode**.
* A **screen** is any other Anthias device that has joined the
  controller. It keeps playing on its own, also when the controller or
  the network is down, and the controller updates it whenever you
  publish.

A screen belongs to one controller at a time. A controller does not
manage other controllers.

## Recommended hardware

The controller does more work than a screen. On every publish it sends
each screen its own copy of every changed file, it keeps a connection
open to every screen, and it serves the management pages. A screen only
receives and plays.

| Role | Recommended | Works, but not recommended | Avoid |
|---|---|---|---|
| **Controller** | Raspberry Pi 5, Raspberry Pi 4 with 2 GB of RAM or more, or a 64-bit x86 PC | Raspberry Pi 4 with 1 GB of RAM | Raspberry Pi 2 and 3 |
| **Screen** | Any device Anthias supports: Raspberry Pi 2, 3, 4 and 5, 64-bit x86 PCs, and (best effort) Rock Pi 4 and other Armbian boards | | |

Why the controller needs the bigger device:

* **Memory.** A 1 GB board already runs close to its limit just playing
  content. In our tests, publishing to three screens at once used up
  almost all of a 1 GB Raspberry Pi 4's swap space. It recovered, but
  there is no headroom left for larger playlists or more screens.
* **Network and CPU.** Publishing is limited by how fast the controller
  can send files. The Raspberry Pi 2 has 100 Mbit Ethernet and the
  Raspberry Pi 3's Ethernet shares a USB bus. All management traffic is
  encrypted, which costs noticeable CPU on the older boards.

Other recommendations:

* Use **wired Ethernet** for the controller, and preferably for the
  screens too.
* Give the controller (and ideally every screen) a **fixed IP address**
  or a DHCP reservation, or reach it by a DNS name. Screens remember
  the address the controller had when they joined.
* The controller needs storage for its own playlist only. Each screen
  needs enough free space for the published playlist (the Screens page
  warns when a screen drops below 1 GB free).

**How many screens?** It is designed for a handful up to a few dozen
screens on one network. Publishing sends each screen its own copy, so
the time grows with the number of screens and the size of the playlist.
As a reference, publishing about 96 MB (an image, a 48 MB video and
some web pages, plus the controller's existing assets) took 7 to 27
seconds per screen in our tests, the slowest being a Raspberry Pi 2.

## Network requirements

* The controller and its screens must be on the **same local network**,
  or on networks routed to each other without NAT in between. There is
  no cloud service in the middle.
* **Port 8443 (TCP)** must be open in both directions between the
  controller and each screen. Anthias opens it only while a device is a
  controller or a managed screen, and never while authentication is
  turned off.
* The web interface stays on ports 80 and 443 as usual.
* There is no automatic discovery. You add a screen by pasting the
  controller's join information on that screen; the screen tells the
  controller its address.

## Set up the controller

1. On the device that will be the controller, open **Settings** and turn
   on **Authentication**. Controller mode requires it.
2. Turn on **Controller mode** and click **Save settings**. A **Screens**
   tab appears.
3. Open **Screens**. Under **Join information** you'll find a snippet that
   starts with `anthias-join:`. It contains the controller's address, the
   fingerprint of its certificate, and a secret. Click **Copy**.

You can use the same join information for every screen. **Regenerate**
makes the current one stop working, for example if it was shared by
mistake. Screens that already joined are not affected.

## Add a screen

1. On the screen, open **Settings** and turn on **Authentication**.
   Joining is refused while authentication is off, because the screen's
   API would then be open to anyone on the network.
2. Go to **Settings → Multi-screen management** and paste the join
   information.
3. If the controller can't reach the screen at the address it connects
   from (for example, the screen is behind NAT), enter the screen's IP
   address or DNS name in **This screen's address**.
4. Click **Join controller**.

Joining takes a second or two. The screen then shows a **Managed by …**
banner on every page, and it appears on the controller's **Screens**
page as **Online**.

**Content on a managed screen is replaced when the controller
publishes.** Assets you add or change directly on the screen are removed
on the next publish. The banner on every page is there as a reminder.

## Publish and control

From the controller's **Screens** page:

* **Publish to all screens**, or to a single screen. Each screen's
  playlist is made to match the controller's: the same assets, schedules
  and order. Files that haven't changed are not uploaded again, so
  publishing an unchanged playlist takes about a second. The result
  shows per screen.
* **Previous asset**, **Next asset**, **Display on** and **Display off**
  on every screen or on one. Display on/off needs a display that
  supports HDMI-CEC.
* **Reboot** every screen or one. A bulk reboot never reboots the
  controller itself.
* **Check now** refreshes the status of every screen immediately.
* **Remove** a screen. Its access token is revoked on the screen, and
  it keeps playing what it already has.

The **Activity** list records every join, publish and command, and who
did it.

Video lengths are measured by each screen, so a video's duration on a
screen can differ slightly from the value on the controller.

## Status and health

Each screen keeps a connection open to the controller and reports its
own status:

* a heartbeat every minute;
* its health whenever a warning appears or clears, and at least every
  15 minutes.

Because the screen reports in, the controller notices quickly when
something happens:

| What happened | When the controller shows it |
|---|---|
| A screen shuts down, restarts or its Anthias stops | Offline within a couple of seconds |
| A screen's network drops without warning | Offline within about 30 to 60 seconds |
| The screen or the controller comes back | Online again within about 10 to 30 seconds |

If a screen can't keep that connection open (for example, a firewall
blocks it), the controller falls back to checking that screen itself
every 15 minutes. **Check now** checks every screen right away.

For each online screen, the **Screens** page shows free disk space, free
memory and uptime, and flags anything that needs attention:

* **Low disk space**: less than 1 GB free.
* **Storage problems**: the SD card is wearing out, the file system has
  reported errors, it is full, or it is failing.
* **Under-voltage**: now, or earlier since the screen started. This
  usually means a weak power supply or cable, and can cause crashes and
  SD card corruption.
* **Low memory**: less than 64 MB free.

The header shows how many screens need attention.

## Security

Management traffic never uses passwords and never leaves your network:

* **Every device has its own identity.** On first start, each Anthias
  device creates a long-lived, self-signed certificate. Its fingerprint
  is shown under **Settings → HTTPS certificate → Screen identity**.
* **Joining is pinned from the first connection.** The join information
  carries the controller's certificate fingerprint, so a screen only
  ever talks to that controller. In return, the screen gives the
  controller its own fingerprint and an access token, and the controller
  checks that fingerprint on every connection. Nobody else on the
  network can pretend to be either side.
* **A separate port for management.** Port 8443 always presents the
  device's own certificate and only serves the management API.
  Uploading a certificate for the web interface doesn't affect it.
* **Screens identify themselves with their certificate.** For the status
  connection, a screen presents its own certificate as a TLS client
  certificate, and the controller accepts only the one it recorded when
  the screen joined.
* **Tokens are revocable and stored safely.** The screen keeps only a
  hash of the controller's token. The controller stores screen tokens
  encrypted, with a key that is not part of backups.

### Backups

A device's identity certificate is not included in backups. Restoring
one screen's backup onto another device does not clone its identity.
The same goes for the controller's token key: if you restore a
controller's backup onto a different device, the screens have to join
again.

## Leaving and removing

* On a screen, **Settings → Leave management** revokes the controller's
  token immediately and closes port 8443. Revoking the `Controller: …`
  token under **Settings → API tokens** does the same.
* On the controller, **Remove** revokes the token on the screen and
  forgets it.

A screen that left shows as **Offline** on the controller, with a note
that it may have left management. Remove it there, or join it again.

## Usage statistics

If you haven't opted out of anonymous usage statistics, Anthias reports
once a day whether a device is a controller or a managed screen. A
controller also reports counts for the screens it manages: how many
there are, how many are online or offline, how many publishes succeeded
or failed, and how many screens have each kind of health warning. No
names, addresses, certificate fingerprints or content are sent. Opting
out turns this off together with the rest of the usage statistics.

## Troubleshooting

**"The controller's certificate does not match the join information."**
The join information was regenerated, or the address now points at a
different device. Copy the join information from the controller's
**Screens** page again.

**"The controller rejected the join information."** It was regenerated
after you copied it. Copy it again.

**"Could not reach the controller."** Check that the screen can reach
the controller's address on port 8443, and that controller mode is on.

**A screen shows "Offline … not accepting management".** The screen is
up but its management port is closed, usually because it left
management. Join it again or remove it.

**A screen shows "Left management".** Its token was revoked on the
screen. Join it again or remove it.

**Publishing to one screen fails.** The other screens are not affected.
Check that screen's free disk space on the **Screens** page and try
**Publish** for that screen again.
