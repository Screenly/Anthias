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
  what gets published. Choose **Make this a controller** under
  **Settings → Multi-screen management**.
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
   on **Authentication**. A controller requires it.
2. Under **Multi-screen management**, click **Make this a controller**.
   You land on the new **Screens** page.
3. Until a screen has joined, **Screens** shows the steps and the
   **Join information**: a snippet that starts with `anthias-join:`. It
   contains the controller's address, the fingerprint of its
   certificate, and a secret. Click **Copy**.

Later, the same section shows **This screen is a controller**, with a
link to **Screens** and **Turn off controller mode**. Turning
authentication off also turns controller mode off.

You can use the same join information for every screen for one hour.
After that it stops working, so a copy left in a chat or a screenshot
can't be used to add a device later; reload the **Screens** page for
new join information. **Regenerate** makes the current one stop working
at once, for example if it was shared by mistake. Screens that already
joined are not affected. The controller only calls a joining screen
back on port 8443, never on its own loopback or internal addresses.

## Add a screen

1. On the screen, open **Settings** and turn on **Authentication**.
   Joining is refused while authentication is off, because the screen's
   API would then be open to anyone on the network.
2. Go to **Settings → Multi-screen management**, and under **Join a
   controller** paste the join information.
3. If the controller can't reach the screen at the address it connects
   from (for example, the screen is behind NAT), open **Advanced** and
   enter the screen's IP address or DNS name.
4. Click **Join controller**.

The screen now shows **Waiting for approval** with a short **check
code**, such as `K7QX-4MRA-9ZTD`. Nothing is trusted yet: the request only
appears on the controller.

5. On the controller's **Screens** page, the screen is listed under
   **Waiting for approval** with its name, address, model and check
   code. Make sure the check code matches the one on the screen, then
   click **Approve**. If you don't recognize the request, click
   **Reject**.

Once approved, the screen appears on the controller's **Screens** page
as **Online**, and its own web interface turns off (see below). A request that nobody approves expires after 24 hours; the
screen can withdraw it with **Cancel request**.

### A managed screen's web interface is off

From then on the controller is the only place to change the screen.
Opening the screen's own address shows a **Managed by …** page instead
of the usual interface, and its local API answers with an error. Port
8443, which the controller uses, keeps working, and so does playback.

If the controller is gone, for example it was replaced and its screens
can't reach it, sign in on that page and use **Leave management** to
take the screen back. Its web interface comes back at once.

## Publish and control

The controller's **Screens** page lists, from the top: requests
**Waiting for approval** (the **Screens** tab shows how many), your
screens, **Publish** and **Control all screens**, the **Join
information** (folded away once a screen has joined) and **Activity**.
From there:

* **Publish to all screens**, or to a single screen; both ask first,
  because assets added directly on a screen are removed. Each screen's
  playlist is made to match the controller's: the same assets, schedules
  and order. Files that haven't changed are not uploaded again, so
  publishing an unchanged playlist takes about a second. The result
  shows per screen.
* **Previous asset**, **Next asset**, **Display on** and **Display off**
  on every screen, or on one from its **⋯** menu. Display on/off needs a display that
  supports HDMI-CEC.
* **Reboot** every screen or one. A bulk reboot never reboots the
  controller itself.
* **Check now** refreshes the status of every screen immediately.
* **Remove** a screen, from its **⋯** menu. Its access token is revoked on the screen, and
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
* **Every join is approved by a person.** Knowing the join information
  only lets a device ask to join. It is not managed until someone
  approves it on the controller, after comparing the check code. The
  check code is worked out from the join information's secret and both
  devices' certificates, so it only matches when this screen used this
  controller's join information and the controller sees this screen's
  certificate. A different device asking in its name shows a different
  code, and with 12 characters it can't be made to match by trying
  many keys.
* **The controller is a small certificate authority.** When it is
  approved, the screen gets a client certificate signed by the
  controller. The screen creates the signing request with its own key,
  which never leaves the device. The controller has a client
  certificate of its own too.
* **Mutual TLS in both directions.** Once a screen is managed, its
  port 8443 only accepts connections that present the controller's
  certificate. The controller in turn knows screens by the certificate
  it issued them, for example on the status connection. Both sides
  still pin each other's identity certificate as before.
* **Certificate authority and key pinning, together.** Every
  management connection has to pass two separate checks. The
  certificate must come from the controller's certificate authority
  and not be revoked. It must also be for the device key that was
  recorded when the screen joined. The screen records the controller's
  key from the certificate it already pinned in the join information,
  and the controller records the screen's key from the certificate it
  reached the screen on. A certificate made for any other key is
  refused, even one signed by the controller's own authority, for
  example with a stolen authority key. Renewed certificates are for the
  same keys, so renewals keep working without anything to re-pin.
  The controller also only signs a screen's request for the key it
  recorded when that screen asked to join.
* **Certificates renew themselves.** Client certificates are valid for
  a year. A screen asks for a new one over its status connection when
  less than 30 days are left (once per connection), and the controller
  renews its own automatically. The old certificate keeps working
  until the screen has connected with the new one, so a renewal lost
  on the way never locks a screen out. If a screen has no status
  connection, or still hasn't renewed with 15 days left, the controller
  renews it over port 8443 instead. Nothing needs to be done by hand.
* **The certificate authority fails safe.** It is created only on a
  controller that has none. If its files exist but can't be read (a
  failing SD card, a half-restored backup), the controller does not
  replace them, because a new authority would cut off every screen.
  The **Screens** page says what is wrong until the files are fixed.
* **Revocation list.** When you remove a screen, or a certificate is
  replaced, the controller revokes the old certificate. Revoked
  certificates are listed in a signed revocation list (CRL). Connected
  screens get each new list at once; the controller re-signs it at
  least every five days, and screens refuse a controller certificate
  that is on it. A certificate drops off the list once it would have
  expired anyway.
* **Authentication comes first.** Joining and controller mode need it,
  and every Anthias device shows a warning on each page while it is
  off, because anyone on the network could then change the screen.
* **A separate port for management.** Port 8443 always presents the
  device's own certificate and only serves the management API.
  Uploading a certificate for the web interface doesn't affect it.
* **Tokens are revocable and stored safely.** The screen keeps only a
  hash of the controller's token. The controller stores screen tokens
  encrypted, with a key that is only in a backup if you choose to
  include the device keys (below).
* **The controller's token is narrow.** The screen accepts it only on
  port 8443 and only for the calls the controller makes (assets,
  playlist order, playback, display power, reboot and pairing). It
  can't create or restore backups.

### Backups

By default, a backup holds no private keys: not the device's identity
certificate, not the controller's certificate authority, not the key
that protects stored tokens. Restoring such a backup onto another
device does not clone its identity, and a copied backup file can never
be used to vouch for a device. The catch: if you restore a
controller's backup onto new hardware, every screen has to join again.

To move a controller (or a screen) to new hardware without that, turn
on **Include this device's keys** under **Settings → Backup &
restore** and choose a password before clicking **Get backup**. The
keys are added to the backup encrypted with that password
(AES-256-GCM, with the key derived from your password by scrypt), the
same idea as the encrypted configuration backups in pfSense and
OPNsense. They can't be added without a password.

To restore them, enter that password in **Keys password** before
uploading the backup. Without it, everything else is restored and the
keys are left out. A wrong password restores nothing.

Restoring a backup revokes every API token in it and ends
multi-screen management on a screen, so a restored backup can't bring
back old credentials. Join the screen again afterwards.

Keep a backup with keys as safe as the device itself: whoever has it
and the password can stand in for the controller. Restore it on one
device only, and retire the old one; two controllers with the same
keys would both answer for the same screens.

## Leaving and removing

* On a screen, sign in on its **Managed by …** page and use **Leave
  management**. That revokes the controller's token immediately, closes
  port 8443 and brings the screen's web interface back.
* On the controller, **Remove** revokes the token on the screen,
  revokes the screen's client certificate, and forgets it.

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

**The screen keeps showing "Waiting for approval".** Approve it on the
controller's **Screens** page. If it isn't listed there, the request
expired or was rejected: click **Cancel request** on the screen and
join again.

**The check codes don't match.** Don't approve the request. Reject it,
and check which device is using that screen's address.

**"That request has expired."** Nobody approved it within 24 hours.
Click **Cancel request** on the screen and join again.

**"This controller's certificate authority is unusable."** The
controller's `ca.key` or `ca.crt` (in `~/.anthias/ssl/controller/`)
can't be read. The controller leaves them alone so its screens keep
working once they are back. Restore them from a backup that includes
the device keys. Only if that isn't possible, remove both files: the
controller then creates a new authority, and every screen has to join
again.

**"Could not deliver the approval."** The controller couldn't reach the
screen on port 8443 when you approved. The request stays listed;
check the network and approve again.

**"The request does not record its key."** The request was made
before an update. Reject it and join the screen again.

**Right after an update, a managed screen refuses the controller for a
moment.** Screens that were approved before an update record the
controller's key the next time they connect to it, which is normally
within a minute. Until then the screen refuses the controller's calls
on port 8443 rather than trust a certificate it can't check. If it
lasts, check that the screen can reach the controller on port 8443.

**"Could not reach the controller."** Check that the screen can reach
the controller's address on port 8443, and that controller mode is on.

**A screen shows "Connected, not reachable".** It reports in to the
controller, but the controller can't call it on port 8443, so it can't
be published to. Check that nothing (a firewall, port forwarding)
blocks port 8443 from the controller to the screen; the status clears
on the next successful check.

**A screen shows "Offline … not accepting management".** The screen is
up but its management port is closed, usually because it left
management. Join it again or remove it.

**A screen shows "Left management".** Its token was revoked on the
screen. Join it again or remove it.

**Publishing to one screen fails.** The other screens are not affected.
Check that screen's free disk space on the **Screens** page and try
**Publish** for that screen again.
