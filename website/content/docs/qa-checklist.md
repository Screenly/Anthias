---
title: "QA Checklist"
description: "Manual testing checklist for Anthias."
slug: "qa"
aliases:
  - "/docs/qa-checklist/"
---

Running unit tests is a good way to make sure that the code is working as expected. However, it's also important to do manual testing as some bugs may not be caught by unit tests. This document contains a list of things that you can do to test the application manually.

> **Note**
>
> The list is not exhaustive, but you can use it as your guide when testing Anthias.

## General

1. Make sure that device is connected to the internet (e.g., via Ethernet).
2. Turn on the device and wait for the splash page to appear.
3. Make sure that the splash page is being displayed properly.
4. Use the provided IP address and make sure that entering it in a browser will redirect you to the web UI home page (the assets page).

## Content page

1. Add assets (image, video, or webpage) and make them active by toggling the switch.
2. Make sure that these assets are shown on the screen.
3. Disable the assets and make sure that the screen is in standby mode, which means that it displays the Anthias standby page.
4. Change a duration for any asset and make sure that it is being displayed for the specified duration.
5. Change the start and end dates and make sure that the asset is being displayed only during the specified period.
6. Turn on some assets and change their order (by dragging and dropping them). Make sure that the assets are being displayed in the correct order.
7. Try to change a name of any asset.
8. Turn on some assets. Click on Previous asset and on Next asset. Make sure that the screen displays the asset that comes before or after the current asset.
9. Click on the download button near any asset. The asset should be downloaded into your computer.

## Settings page

1. Go to the Settings page.
2. If desired, change the device's **Player name**.
3. Change a default value for the **Default duration** and upload any asset. Make sure that the duration value is correct.
4. Change a default value for the **Default streaming duration** and upload any stream. Make sure that the asset is being displayed for the specified duration.
5. Enable **Show splash screen** and reboot the device. Make sure that the splash screen is being displayed upon boot.
6. Enable **Default assets** and make sure that the default assets are added to the list of active assets. Also make sure that the assets are being displayed on the screen.
7. Disable **Default assets** and make sure that the assets are deleted from the list of active assets. Also make sure that the assets are not being displayed on the screen.
8. Enable **Shuffle playlist**. Activate some assets and make sure that the assets are being displayed in random order.
9. Enable **Use 24-hour clock**. Go to the assets page and make sure that the time field uses correct format.
10. Enable any video asset with sounds and choose **HDMI** for the **Audio output**. Make sure that the sound works.
11. Enable any video file with sounds and choose **3.5mm** jack for the **Audio output**. Make sure that the sound works.
12. Choose any format for the **Date format**. Go to the assets page and make sure that the date field uses the correct format.
13. Enable authentication by selecting **Basic** from the **Authentication** dropdown, set a username + password, save, then reload. You should be prompted to log in. Verify both credential paths still reach the API:
    * Browser session: log in via the form, dashboard works.
    * Legacy HTTP Basic: `curl -u user:pass http://<device>/api/v2/assets` still works, and the server logs a `DEPRECATED: HTTP Basic auth used …` warning.
    * WebSocket: with auth on, an unauthenticated `/ws` handshake is refused (`curl -i -N -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' http://<device>/ws` returns 403, not 101). Logged in, the dashboard still refreshes instantly when an asset changes — not only on the 5s poll. Rotating the operator's password while that dashboard is open drops its socket, and the tab falls back to the 5s poll until it is logged in again.
14. Click the **Get Backup** button. Delete all assets. Click on **Upload and Recover** and make sure that the assets are restored.
15. Try to reboot or shutdown the device by clicking on the **Reboot** or **Shutdown** buttons, respectively. Make sure that the device does the corresponding action.
16. Go to the **System Info** page and make sure that all information are correct.
17. Make sure that all the footer links are being displayed correctly.
18. Connect to the device by ssh. Run `./bin/enable_ssl.sh` script. Make sure the site URL uses SSL.

## WiFi setup (captive portal)

Needs a board with a WiFi adapter, installed with the "manage the network" prompt enabled. See [WiFi Setup](/docs/wifi-setup/).

1. On a device that is already online, reboot and confirm that **no** setup network appears and the display goes straight to the splash page. `journalctl -u anthias-wifi-connect.service -b` should show `Skipped due to 'exec-condition'` — the check refuses before wifi-connect runs at all, so there is no container log to read in this case.
2. Forget the device's saved WiFi (`nmcli connection delete <name>`), unplug Ethernet, and reboot. Within about a minute the display should switch to the WiFi setup page showing a network name, a password, and a QR code.
3. Scan the QR code with a phone. It should join **Anthias Setup** without you typing the password.
4. Open `http://192.168.42.1:8000` on the phone, pick a network, and enter its password. The device should join it, the setup network should disappear, and the display should go back to the playlist (or standby) on its own, with no reboot.
5. Repeat step 4 with a **wrong** password. The portal should come back and say the password was wrong, the phone should rejoin the setup network by itself, and a second attempt with the right password should work.
6. While the setup page is up, confirm the splash page's address list does **not** offer `192.168.42.1` as a way to manage the device.
7. On an Ethernet-only x86 box, reboot and confirm `systemctl status anthias-wifi-connect.service` is not in a failed state — no WiFi adapter is an expected outcome, not an error.
8. Leave the setup page up for 15 minutes without completing setup. The setup network should come down by itself and the device should return to normal — and then come back a few minutes later, because the check repeats. `systemctl list-timers anthias-wifi-connect.timer` shows when the next one is due.
9. Let that happen three times without completing setup. The fourth attempt should **not** come a few minutes later: the device backs off to one attempt every six hours so an unattended screen keeps playing content. `journalctl -u anthias-wifi-connect` says how long it is waiting, and `cat /run/anthias-wifi-connect/raises` shows the count. Rebooting clears it and the quick cadence returns.
9. On a device whose WiFi *is* connected, run `sudo systemctl start anthias-wifi-connect.service` by hand. It must skip with `Skipped due to 'exec-condition'` and log *"WiFi is already configured and associated"* — raising an access point here would tear down the working WiFi.
10. Same again on a device with only a wired connection: it must skip with *"a wired network is connected"*.
11. Run `bin/upgrade_containers.sh` on an online device with WiFi configured. The upgrade must NOT raise a setup network — `anthias-wifi-connect` should end up `Created`, never `Up`. This is the path that bypasses the systemd gate if the compose profile is ever dropped.
