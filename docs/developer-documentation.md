## Screenly-OSE Developer Documentation
_(this document is in its initial phase)_

Here is a high-level overview of the different components that make up the Screenly-OSE system.
![Screenly-OSE Diagram Overview](https://raw.githubusercontent.com/screenly/screenly-ose/master/docs/images/screenly-ose-diagram-overview.png)

* Screenly-Viewer is what drives the screen (Ex: shows web page or image or video)
* Screenly-Server is what the user interacts with (Ex: Web GUI)
* Screenly-Celery is for task/queue/asynchronously executing work outside the HTTP request-response cycle (Ex: periodic cleanup task, upgrade via web)
* Screenly-WebSocket is used for forwarding requests from NGINX to backend Screenly-Server (Ex: )
* Redis is used as a database, cache and message broker. (Ex: )
* SQLite is used as the database for storing the assets information.

These components and their dependencies are mostly installed and handled with Ansible and respective playbooks.

There are currently three versions of Screenly-OSE..

| Version       | Branch     | Comment    |
| :------------- | :---------- | :----------- |
|  Developer | [master](https://github.com/Screenly/screenly-ose)   | This is where we test things and apply latest fixes   |
|  Production | [production](https://github.com/Screenly/screenly-ose/tree/production)   | This is the branch disk images are built from and should be properly tested    |
|  Experimental | [experimental](https://github.com/Screenly/screenly-ose/tree/experimental)   | This is the branch with the experimental browser version mentioned above    |


### Docker and Directories

/

### Directories, files and their purpose with regards to Screenly

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

matchbox.service ->
screenly-celery.service ->
screenly-viewer.service ->
screenly-web.service ->
screenly-websocket_server_layer.service ->
wifi-connect.service ->
```

```
/etc/nginx/sites-enabled/

screenly_assets.conf ->
screenly.conf ->
```

```
/etc/sudoers.d/screenly_overrides -> sudoers configuration file that allows pi user to execute certain sudo commands without being superuser.
```

```
/usr/share/plymouth/themes/screenly

screenly.plymouth ->
splashscreen.png ->
screenly.script ->
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
```
