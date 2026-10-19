---
title: "WiFi Setup"
description: "How Anthias gets a screen onto WiFi when it has no network yet, using the wifi-connect captive portal."
slug: "wifi-setup"
aliases:
  - "/docs/wifi-setup/"
---

A screen that has never been on a network has a chicken-and-egg problem: it
needs WiFi credentials, and the only way to hand it any is over a network it
doesn't have. Anthias solves this the usual way — the screen raises its own
temporary WiFi network, shows you how to join it, and takes the credentials
from a page in your browser.

The tool that does this is
[wifi-connect](https://github.com/Screenly-Labs/wifi-connect), which runs as
the `anthias-wifi-connect` container alongside the rest of Anthias.

## What you'll see

When a screen can't reach a network, it puts a setup page on the display
showing three things:

* the name of the temporary WiFi network it has created (**Anthias Setup**),
* the password for it, which is different every time,
* a QR code that joins that network for you, so you don't have to type the
  password.

Scan the QR code with a phone, or join **Anthias Setup** by hand using the
password shown. Then open **http://192.168.42.1:8000** in a browser, pick the
network the screen should use, and enter its password. The screen joins it and
goes back to playing your content.

> **Note**
>
> The password on screen is only valid for that one setup session. If the
> screen gives up and tries again, it generates a new one and the display
> updates — so read it off the screen rather than noting it down.

## When it appears

The setup network only comes up when the screen genuinely can't get online,
and only once per boot:

1. On boot, Anthias lets NetworkManager try the networks the screen already
   knows. Associating, getting an address and checking the connection takes
   15–30 seconds from cold, so it waits up to a minute for that to finish.
2. If a known network comes up, nothing else happens — no setup network, no
   setup page.
3. If nothing comes up (or the screen has never been on WiFi at all), the
   setup network appears and the display switches to the setup page.
4. After 15 minutes with nobody completing setup, the setup network comes down
   and the screen goes back to what it was doing. Reboot it to try again.

Anthias checks for itself whether the screen can reach the internet before it
raises anything, and skips the whole thing when it can. That check is Anthias's
own rather than NetworkManager's on purpose: NetworkManager ships on Raspberry
Pi OS and Debian without its connectivity test compiled in, so asking it always
returns "don't know" — and "don't know" would mean a setup network on every
boot of every screen, including ones that are perfectly online.

The 15-minute limit is the backstop for the same concern. If the check is ever
wrong — a restricted network that blocks the probe, say — a wrong answer costs
you one window rather than taking the screen off the air indefinitely.

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
run:

```bash
sudo systemctl disable --now anthias-wifi-connect.service
```

Re-running the Anthias installer with the "manage the network" prompt
disabled does the same thing and removes the unit.

## Security

The setup network is encrypted (WPA2/WPA3) with a password generated fresh for
each session, and it is only up while setup is actually happening. That's a
deliberate change from the captive portal Anthias used before 2026.04, which
created an *open* network — everything typed into it, including your WiFi
password, crossed the air in the clear.

The encryption protects you from anyone who isn't on the setup network. It
does not protect you from someone who *is* — a second device that joined the
same setup network at the same time could capture the handshake. The password
being different every time and the network being short-lived is what bounds
that; it isn't a wall between two devices on the same setup network.

## Troubleshooting

**The setup page never appears.** Check that the service ran, and what it
decided:

```bash
journalctl -u anthias-wifi-connect.service -b
docker logs anthias-wifi-connect
```

Common outcomes, all of them logged in plain language: no WiFi adapter found,
the adapter can't do access-point mode, NetworkManager isn't managing the
interface, or the screen was already online.

**I joined the setup network but the page doesn't open by itself.** Open
<http://192.168.42.1:8000> manually. Phones running iOS 14+, Android 11+,
recent macOS and Windows 11 are told where the page is as part of getting an
address and will open it for you; older devices won't, because the port a
captive portal is normally found on (80) is already serving the Anthias web
interface on this device.

**The screen keeps dropping into setup mode.** That means Anthias's internet
check is failing — on a network that blocks outbound requests this can happen
even when everything else works. Check what it decided:

```bash
journalctl -u anthias-wifi-connect.service -b | head
```

A line reading *"no internet after 6 attempts"* on a screen you believe is
online is the symptom. Configure WiFi over SSH as above and disable the
service.
