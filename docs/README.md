Welcome to the Screenly OSE

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
To access the console while Screenly OSE is Running Simply hit `CTRL + ALT + F1`

# Enabling SSH

See [the official documentation](https://www.raspberrypi.org/documentation/remote-access/ssh/)

# Updating Screenly
From a Console Run:

`bash <(curl -sL https://www.screenly.io/install-ose.sh)`

# Accessing the SQLite Database

**This section is for power users only; DO NOT mess around with the database unless you know what you are doing**.

For most users, it's recommended that you use the API instead.

The SQLite Database can be found here: `~/.screenly/screenly.db`

Its a SQLite Database and can be modified with the sqlite3 CLI. The schema is relatively straight forward if you are a developer; the columns of most interest to you will be `name` and `is_enabled`. In addition `start_date` is useful if you want to use this in a disconnected manner.

# Wi-Fi Setup

We've attempted to restore the Wi-Fi connectivity feature by introducing Balena's [wifi-connect][1] as a Docker
service. At present, a lot of Wi-Fi connectivity issues have been reported. As a result, we temporarily disabled
Wi-Fi connectivity in instances running Raspberry Pi OS Lite.

For Raspberry Pi devices running Raspberry Pi OS Lite, you can run the following in order to stop the
`wifi-connect` service:

```bash
docker stop screenly-anthias-wifi-connect-1
```

Alternatively, you could do it via `docker compose`:

```bash
docker compose stop anthias-wifi-connect
```


[1]: https://github.com/balena-os/wifi-connect
