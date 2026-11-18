---
title: "WiFi Setup"
description: "How Anthias gets a screen onto WiFi when it has no network yet, using the wifi-connect captive portal."
slug: "wifi-setup"
---

A screen that has never been on a network has a chicken-and-egg problem: it
needs WiFi credentials, and the only way to hand it any is over a network it
doesn't have. Anthias solves this the usual way — the screen raises its own
temporary WiFi network, shows you how to join it, and takes the credentials
from a page in your browser.

The tool that does this is
[wifi-connect](https://github.com/Screenly-Labs/wifi-connect), which runs as
the `anthias-wifi-connect` container alongside the rest of Anthias.

> **Note**
>
> Available on both installation paths — Raspberry Pi OS / Debian and balena.
> The two differ in how often a screen retries: on Debian a timer re-checks
> every five minutes for the first three attempts and then every six hours,
> while on balena the setup network comes up once per boot (and once per
> release apply). Power-cycling a screen asks for setup again on either.

## What you'll see

When a screen can't reach a network, it puts a setup page on the display
showing three things:

* the name of the temporary WiFi network it has created — **Anthias Setup**
  followed by four characters unique to that screen, like
  **Anthias Setup-G4VV**,
* the passphrase for it, which is different every time,
* a QR code that joins that network for you, so you don't have to type the
  passphrase.

Scan the QR code with a phone, or join the network by hand using the name and
passphrase shown. The four characters on the end of the name are what tell two
screens apart: unbox five in one room and each creates its own network, so the
one you join is the one you are standing in front of. Then open **http://192.168.42.1:8000** in a browser, pick the
network the screen should use, and enter its passphrase. The screen joins it and
goes back to playing your content.

> **Note**
>
> The passphrase on screen is only valid for that one setup session. If the
> screen gives up and tries again, it generates a new one and the display
> updates — so read it off the screen rather than noting it down.

## When it appears

Anthias checks, shortly after boot and then every few minutes, whether the
screen needs setting up at all. It skips the whole thing — no setup network,
nothing on the display — if **any** of these is true:

* the screen's WiFi is already connected to a network,
* it has a working wired connection,
* it can reach the internet by any route.

Only a screen with none of those gets a setup network. That is the first-boot
case the portal exists for, and every one of those conditions errs towards
leaving a working screen alone.

The check is Anthias's own rather than NetworkManager's on purpose.
NetworkManager ships on Raspberry Pi OS and Debian without its connectivity
test compiled in, so asking it always returns "don't know" — and "don't know"
would mean a setup network on every boot of every screen, including ones that
are perfectly online.

If nobody completes setup within 15 minutes, the setup network comes down and
the screen goes back to what it was doing until the next check.

Because it repeats, a screen that nobody gets to within one setup window is
offered another one, and a screen whose router is replaced months later gets
the setup network back on its own. You don't have to reboot it.

It does slow down, though. The setup screen replaces your content while it is
up, so after **three** attempts with nobody completing setup, Anthias drops to
**one attempt every six hours** instead of one every few minutes. On a site
with no network to join — an airgapped installation, or a screen whose access
point is gone for good — that is the difference between a screen showing the
setup page three quarters of the time and one showing your content with a
brief interruption four times a day.

It never stops entirely, because a network can appear at any time and nobody
would be there to notice. To get the quick cadence back, power cycle the
screen: a reboot clears the count.

A screen that has never been able to download the setup tool — an airgapped
install, or one provisioned while the registry was unreachable — skips the
whole thing quietly rather than retrying something it cannot run. Re-run the
upgrade once it can reach the registry.

## Requirements

* **The installer's "manage the network" prompt must be enabled.** This is
  what installs NetworkManager and what Anthias treats as your permission to
  touch the screen's networking at all. Answer *No* and the setup network is
  never raised.
* **The WiFi adapter has to support access-point mode.** Most do. Known
  exceptions include the BCM43143, MT7601 and RTL8188CUS chipsets.
* **A wired-only machine does nothing here.** The service notices there is no
  WiFi adapter and exits; this is expected and not an error.

## If you'd rather not use it

Everything here is optional. You can still configure WiFi over SSH with
`sudo nmtui` (interactive) or
`nmcli device wifi connect <SSID> password <password>`, and a screen set up
that way never shows the setup page.

To turn the captive portal off on a screen that already has it, SSH in and
disable the timer — that's what schedules the checks:

```bash
sudo systemctl disable --now anthias-wifi-connect.timer
```

Re-running the Anthias installer with the "manage the network" prompt
disabled does the same thing and removes the unit.

## Security

The setup network is encrypted (WPA2/WPA3) with a passphrase generated fresh for
each session, and it is only up while setup is actually happening. That's a
deliberate change from the captive portal Anthias used before 2026.04, which
created an *open* network — everything typed into it, including your WiFi
passphrase, crossed the air in the clear.

The encryption protects you from anyone who isn't on the setup network. It
does not protect you from someone who *is* — a second device that joined the
same setup network at the same time could capture the handshake. The passphrase
being different every time and the network being short-lived is what bounds
that; it isn't a wall between two devices on the same setup network.

## Troubleshooting

**The setup page never appears.** Check that the service ran, and what it
decided:

```bash
systemctl list-timers anthias-wifi-connect.timer
journalctl -u anthias-wifi-connect.service -b
docker logs anthias-wifi-connect
```

Each check says in plain language what it decided — *"WiFi is already
configured and associated"*, *"a wired network is connected"*, *"the device
can reach the internet"* — or, if it did run, what wifi-connect found: no WiFi
adapter, an adapter that can't do access-point mode, or an interface
NetworkManager isn't managing.

**I joined the setup network but the page doesn't open by itself.** Open
[the setup page at 192.168.42.1:8000](http://192.168.42.1:8000) manually. Phones running iOS 14+, Android 11+,
recent macOS and Windows 11 are told where the page is as part of getting an
address and will open it for you; older devices won't, because the port a
captive portal is normally found on (80) is already serving the Anthias web
interface on this device.

**The screen keeps dropping into setup mode.** All three checks are failing.
On a screen with no WiFi and no wired link, that is the internet probe on a
network that blocks outbound requests. The symptom is this line, on a screen
you believe is fine:

```
no WiFi association, no wired network and no internet after 6 checks;
the WiFi setup portal is needed
```

Configure WiFi over SSH as above — once the screen is associated, the first
check short-circuits and the portal stops being offered — or disable the timer.
