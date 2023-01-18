Welcome to Anthias

How to get the Log out of Screenly for reporting issues:

SSH into the Pi, if you are not sure what this means, and you are Windows:

1. Download [Putty](http://www.chiark.greenend.org.uk/~sgtatham/putty/)
2. Leave everything else as default, change the Host Name to the name or IP address of your pi then click open
3. A black screen will pop up with "login as:" written
4. login as `pi` with the password raspberry (or whatever you changed it to)

On macOS and Linux, there is no need to download any software. You can use `ssh` directly from your Terminal.

Navigate to the /tmp folder (try `cd /tmp`)
Open the log file with your favorite editor (`nano` is easy to use) screenly_viewer.log

In Putty, to copy something simply select it, that its it, it's now magically in your clipboard ready for pasting.

# Console Access
To access the console while Anthias is running Simply hit `CTRL + ALT + F1`

# Enabling SSH

See [the official documentation](https://www.raspberrypi.org/documentation/remote-access/ssh/)

# Updating Screenly
From a Console Run:

`bash <(curl -sL https://install-anthias.srly.io)`

# Accessing the SQLite Database

**This section is for power users only; DO NOT mess around with the database unless you know what you are doing**.

For most users, it's recommended that you use the API instead.

The SQLite Database can be found here: `~/.screenly/screenly.db`

Its a SQLite Database and can be modified with the sqlite3 CLI. The schema is relatively straight forward if you are a developer; the columns of most interest to you will be `name` and `is_enabled`. In addition `start_date` is useful if you want to use this in a disconnected manner.

# Wi-Fi Setup

~~On first boot your Anthias player will check if there is any active network connection (such as Ethernet with DHCP). If there isn’t one, then the Pi will create a local wifi network and display the SSID and PW on the screen. Using your phone or computer connect to this network and navigate to the URL displayed on the screen. (Ex: Screenly.io/wifi)  This will take you to the network setup page for your Anthias player. If you are not connected to the network that the player is generating then you will be redirected here.~~

1. Disconnect the Ethernet cable from your Raspberry Pi.
2. Turn on your Raspberry Pi device.
3. Refresh the web UI page. You shouldn't be able to access the page anymore.
4. Go to your phone or computer and connect to the network whose SSID is "Anthias WiFi Connect".
5. The screen will then display the hotspot page which contains the following information &ndash;
_SSID ("Anthias WiFi Connect")_, _Password (None)_, and _Address ("192.168.42.1:8000")_.
6. Go to your browser and go to the captive portal using the gateway IP address, plus the
port `8000` (e.g., http://192.168.42.1:8000).
7. On the captive portal, select a network to connect to by selecting an item fromt the **_SSID_** drop-down.
8. Input the Wi-Fi password.
9. Once you're all set, click **_Connect_**. Your phone/computer will be disconnected from the access point
(i.e., the Raspberry Pi).
10. Your device will soon be online. Otherwise, the access point will be back up.
