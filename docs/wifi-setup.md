# Connecting to Wi-Fi

There are several ways to connect your Anthias instance to Wi-Fi. You can either
configure the Wi-Fi settings via the captive portal or you can use
`NetworkManager`'s `nmcli` or `nmtui` commands.

> [!WARNING]
> The `nmcli` and `nmtui` commands will only work on devices running a
> Raspberry Pi OS Lite as those running balenaOS gives users less control over
> some configs.

## Using `nmcli`

If you said yes when prompted for network management during the installation
process, its recommended to use the `NetworkManager` (via the `nmcli` command).
For more details, you can read [this article](https://www.makeuseof.com/connect-to-wifi-with-nmcli/)
that shows how to connect to Wi-Fi.

### Enable the Wi-Fi device

Run the following command to check if the Wi-Fi device is enabled.

```shell
$ nmcli dev status
```

The console output (truncated to save space) should look something like this:

```
DEVICE           TYPE      STATE                   CONNECTION
eth0             ethernet  connected               Wired connection 1
br-7569eb45ac38  bridge    connected (externally)  br-7569eb45ac38
docker0          bridge    connected (externally)  docker0
wlan0            wifi      disconnected            --
p2p-dev-wlan0    wifi-p2p  disconnected            --
```

Look for the row with `wlan0` for the `DEVICE` name. Its `STATE` should be
`disconnected`. If it's set to `unavailable`, then it's not enabled.

You can also run `nmcli radio wifi` to check the status of the Wi-fi interface.
The output could either be `enabled` or `disabled`. If it shows that the
interface is `disabled`, run `nmcli radio wifi on`.

### Identify the Wi-Fi access point

If you didn't know the name of your network, run `nmcli dev wifi list`. You'll
get an output the looks like this:

```
IN-USE  BSSID              SSID              MODE   CHAN  RATE        SIGNAL  BARS  SECURITY
        80:75:C3:DF:74:E4  Network27861      Infra  1     260 Mbit/s  100     ▂▄▆█  WPA2 WPA3
# ...
# The output is truncated to save space.
```

> [!NOTE]
> Take good note of the `SSID` (in this example, it's `Network27861`), as you'll
> use it in the next step.

### Connect to Wi-Fi with `nmcli`

You can either do any of the following:

```shell
$ sudo nmcli dev wifi connect $WIFI_SSID password $WIFI_PASSWORD
```

```shell
$ sudo nmcli --ask dev wifi connect $WIFI_SSID
```

> [!WARNING]
> We recommend that you use the second one. You don't want someone to know your
> password just by looking at the command `history`. The output should look like
> the following &mdash; `Device 'wlan0' successfully activated with
> <hex>`.

To see if the Wi-Fi connection is successful, try running a `ping` commad:

```shell
$ ping google.com # You can also use `1.1.1.1`.
```

You can also check to see if the `wlan0` interface is assigned an IPv4 address.
If so, then you're all set.

```shell
$ ip addr show wlan0
```

Here's a sample output:

```
3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP group default qlen 1000
    link/ether [MAC-ADDRESS] brd ff:ff:ff:ff:ff:ff
    inet 10.0.0.25/24 brd 10.0.0.255 scope global dynamic noprefixroute wlan0
       valid_lft [VALID_LFT_HEX] preferred_lft [PREFERRED_LFT_HEX]
# ...
```

### Disconnecting from the Wi-Fi

Just run `sudo nmcli connection delete $WIFI_SSID`.

Some part of the output are hidden for security reasons.

## Using `nmtui`

If [using `nmcli`](#using-nmcli) might be a bit overwhelming for you, you can
use `nmtui` instead. Also a part of the `NetworkManager` package, it's like
`nmcli` but it uses a TUI instead of a CLI.

### Start NetworkManager TUI

Run `sudo nmtui`. You'll be presented with a TUI that looks like this:

![nmtui-01](/docs/images/nmtui-01.png)

Select `Activate a connection` by making use of the arrow keys and press `Enter`.

### Select a Wi-Fi network to connect to

Select a Wi-Fi network that you want to connect to. Use the arrow keys to select
a network and press `Enter`. A part of the image was hidden for security reasons.

![nmtui-02](/docs/images/nmtui-02.png)

You will then be prompted to type in the password for the Wi-Fi network. Press
`Enter` to continue.

![nmtui-03](/docs/images/nmtui-03.png)

The TUI will then bring you back to the list of networks. You'll notice that the
selected network (highlighted in red) now has a `*` beside it. Press `Enter` to
continue.

![nmtui-04](/docs/images/nmtui-04.png)

Use the arrow keys to select `Back` and press `Enter`. You'll be brought back to
the main menu. Select `Quit` and press `Enter`.

### Check if the Wi-Fi connection is successful

```shell
$ ip addr show wlan0
```

You should see an output similar to the one in the
[previous section](#connect-to-wi-fi-with-nmcli).

## Using the captive portal

Anthias uses balena's [WiFi connect][1] to setup Wi-Fi connectivity in Raspberry Pi devices running
either [Raspberry Pi OS Lite][2] or [balenaOS][3].

`wifi-connect` will start if there's no Internet connectivity. For instance, if you unplug the Ethernet
cable from your Pi, a page showing the following information:

- SSID (the Wi-Fi device name), which is "Anthias WiFi Connect" by default
- Password, which is currently not set
- Address, which is `192.168.42.1:8000` by default

Customizing the SSID, password, and IP address is not yet supported.

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

### Limitations and known issues

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

### balenaOS vs Raspberry Pi OS Lite

Wi-Fi setup behaves differently in devices running balenaOS and Raspberry Pi OS Lite.

In Raspberry Pi OS Lite, the device will only start the Wi-Fi setup during startup. After
that, the device will not start the Wi-Fi setup again.

In balenaOS, the device will start the Wi-Fi setup every time the device is not connected to the
Internet. For instance, if you unplug the Ethernet cable from your Pi, the device will start the
Wi-Fi setup after a few moments.


[1]: https://github.com/balena-os/wifi-connect
[2]: https://www.raspberrypi.com/software/
[3]: https://www.balena.io/os
