## Screenly-OSE Developer Documentation
_(this document is in its initial phase)_

Here is a high-level overview of the different components that make up the Screenly-OSE system.
![Screenly-OSE Diagram Overview](https://raw.githubusercontent.com/screenly/screenly-ose/master/docs/images/screenly-ose-diagram-overview.png)

* Screenly-Viewer is what drives the screen (Ex: shows web page or image or video).
* Screenly-Server is what the user interacts with (Ex: Web GUI).
* Screenly-Celery is for task/queue/asynchronously executing work outside the HTTP request-response cycle (Ex: periodic cleanup task, upgrade via web).
* Screenly-WebSocket is used for forwarding requests from NGINX to backend Screenly-Server.
* Redis is used as a database, cache and message broker.
* SQLite is used as the database for storing the assets information.

These components and their dependencies are mostly installed and handled with Ansible and respective playbooks.

There are currently three versions of Screenly-OSE..

| Version       | Branch     | Comment    |
| :------------- | :---------- | :----------- |
|  Developer | [master](https://github.com/Screenly/screenly-ose)   | This is where we test things and apply latest fixes   |
|  Production | [production](https://github.com/Screenly/screenly-ose/tree/production)   | This is the branch disk images are built from and should be properly tested    |
|  Experimental | [experimental](https://github.com/Screenly/screenly-ose/tree/experimental)   | This is the branch for experimenting, such as using a new web browser    |


### Docker and Directories

/

### Directories, files and their purpose with regards to Screenly-OSE
_(Most of the following information pertains to the Production version (Uzbl-based) and not the Developer QtWebview/Docker-based version)_

```
/home/pi/screenly/

All of the files/folders from the Github repo should be cloned into this directory.
```

```
/home/pi/.screenly/

celerybeat-schedule -> stores the last run times of the celery tasks.
default_assets.yml -> configuration file which contains the default assets that get added to the Assets if enabled.
device_id -> randomly generated string to identify device.
initialized -> tells whether hotspot service runs or not.
latest_screenly_sha -> shows the version of branch in hashed value.
screenly.conf -> configuration file for web interface settings.
screenly.db -> database file containing current Assets information.
```


```
/etc/systemd/system/

matchbox.service -> lightweight window manager for the X window system (env variable DISPLAY as 0.0)
screenly-celery.service -> starts the celery worker (App set to server.celery, bpython interface, hostname worker@screenly, schedule database /home/pi/.screenly/celerybeat-schedule)
screenly-viewer.service -> starts the main viewer (viewer.py) and sets a few user prefs for the X display
screenly-web.service -> starts the web server (server.py)
screenly-websocket_server_layer.service -> starts the websocket server, uses zmq for messaging
wifi-connect.service -> starts the resin/balena wifi-connect program to dynamically set the wifi config on the device via captive portal
```

```
/etc/nginx/sites-enabled/

screenly_assets.conf -> configuration file for ngrok.io server, deals with public url tunnel / pro migration?
screenly.conf -> configuration file for nginx web server (default asset settings, web GUI auth, database/asset dir, etc), called by settings.py
```

```
/etc/sudoers.d/screenly_overrides -> sudoers configuration file that allows pi user to execute certain sudo commands without being superuser.
```

```
/usr/share/plymouth/themes/screenly

screenly.plymouth -> plymouth config file (sets module name, imagedir and scriptfile dir)
splashscreen.png -> screenly ose splashscreen image file
screenly.script -> plymouth script file that loads and scales splashscreen image during boot process
```

```
/usr/local/sbin/upgrade_screenly.sh -> bash installation script that gets called through celery task from web interface when users need to upgrade version of screenly to Latest or Production without requiring superuser.
```

```
/usr/local/bin/screenly_usb_assets.sh -> script file that handles assets in USB file.
```

`/other/directories/here/.. from ansible roles`


### Debugging Screenly OSE webview

```
export QT_LOGGING_DEBUG=1
export QT_LOGGING_RULES="*.debug=true"
export QT_QPA_EGLFS_DEBUG=1
```

Screenly OSE WebView is a custom-built web browser based on the [QT](https://www.qt.io/) toolkit framework.
The browser is assembled with a Dockerfile and built by a `webview/build_qt#.sh` script.

For further info on these files and more, visit the following link: [https://github.com/Screenly/screenly-ose/tree/master/webview](https://github.com/Screenly/screenly-ose/tree/master/webview)
