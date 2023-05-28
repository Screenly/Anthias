# Wi-Fi Setup

Anthias uses balena's [WiFi connect][1] to setup Wi-Fi connectivity in Raspberry Pi devices running
either [Raspberry Pi OS Lite][2] or [balenaOS][3].

`wifi-connect` will start if there's no Internet connectivity. For instance, if you unplug the Ethernet
cable from your Pi, a page showing the following information:

- SSID (the Wi-Fi device name), which is "Anthias WiFi Connect" by default
- Password, which is currently not set
- Address, which is `192.168.42.1:8000` by default

Customizing the SSID, password, and IP address is not yet supported.

## Connecting to Wi-Fi

There are several ways to connect your Anthias instance to Wi-Fi. You can either
configure the Wi-Fi settings via the captive portal or you can run the `raspi-config`
on your terminal.

### Using `raspi-config`

Run the following command in your console:

```bash
$ sudo raspi-config
```

A TUI will appear. Follow the steps below:
* Select "System Options" and then select "Wireless LAN".
* You'll be prompted to select the country where you're currently located.
* After that, you'll be asked to enter the SSID and password of the Wi-Fi network that you'd like to connect. If you've enter the correct credentials, you'll be redirected to the TUI main menu.
* Use your arrow keys to navigate to "Finish" and then press Enter.
* To confirm if the device is connected to the Internet via Wi-Fi, run `ip addr show wlan0`. You should be able to see the IPv4 address of the device.

### Using the captive portal

At startup, the device will show the Wi-Fi setup page on a connected display if
the device is not connected to the Internet. *NOTE:* The are times when this feature
does not work. If this happens, you can try running `raspi-config` instead.

To get started, do the following steps:

1.  Make sure that your Raspberry Pi is connected to a display before turning it on.
2.  Turn on your Raspberry Pi.
3.  If the device is not connected to the Internet (for instance, the device is not connected via an
    Ethernet cable).
4.  Wait for the display to show the Wi-Fi setup page, as discussed earlier.
5.  Using your phone or your computer, enable Wi-Fi and connect to "Anthias WiFi Connect". By default
    no password is required.
6.  Open a browser on your phone or computer and go to `192.168.42.1:8000`.
7.  For the SSID, select a Wi-Fi network from the dropdown.
8.  Enter the Wi-Fi network's password.
9.  Click Connect.
10. Your phone will be disconnected from "Anthias WiFi Connect". After some time, you'll notice that
    the display will now show the splash page with an addition IP address.
11. After a minute, the display will show the assets (if there's any).

#### Limitations and Known Issues

- In balenaOS, during boot, if the device is not connected to the Internet, the `wifi-connect` will
  start. However, the screen will not show the Wi-Fi setup page. Instead, the screen will show just
  the splash page. To get the Wi-Fi setup page, you can disconnect the Ethernet cable from the Pi after
  the device has booted and after the splash page has been shown.
- In Raspberry Pi OS Lite, the device will only start the Wi-Fi setup during startup. After that, the
  device will not start the Wi-Fi setup again. To get the Wi-Fi setup page, you can disconnect the
  Ethernet cable from the Pi after the device has booted and after the splash page has been shown.
- During the Pi's boot, the splash page will be shown. After a few moments, the splash page will be
  replaced by the Wi-Fi setup page. It would be better if the Wi-Fi setup page is shown immediately
  after boot.

#### balenaOS vs Raspberry Pi OS Lite

Wi-Fi setup behaves differently in devices running balenaOS and Raspberry Pi OS Lite.

In Raspberry Pi OS Lite, the device will only start the Wi-Fi setup during startup. After
that, the device will not start the Wi-Fi setup again.

In balenaOS, the device will start the Wi-Fi setup every time the device is not connected to the
Internet. For instance, if you unplug the Ethernet cable from your Pi, the device will start the
Wi-Fi setup after a few moments.


[1]: https://github.com/balena-os/wifi-connect
[2]: https://www.raspberrypi.com/software/
[3]: https://www.balena.io/os
