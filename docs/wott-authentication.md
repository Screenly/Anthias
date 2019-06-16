# WoTT credentials based authentication to restrict access to the web GUI

Access to Screenly web configuration interface can be restricted via an [*HTTP basic access authentication*](https://en.wikipedia.org/wiki/Basic_access_authentication) which denies access without proper login and password. Using credentials provided through WoTT credentials system.


## Configuration

After installing [*wott-agent*](https://www.wott.io/) and claiming the device in [*wott-dashboard*](https://dash.wott.io).
Create two credentials with keys `username` and `password` and the same name, by default it is `screenly_credentials`.
If you want, you can change it name for any you like, but it would be lowercase unicode alphanuerics and `_.\-:` characters.
And you also need to set up this name in `~/.screenly/screenly.conf` configuration file, in the `[auth_wott]` part as shown below: 

```
[auth_wott]
; change if you want your own credential name
screenly_credentials=
```

After you set up this two credentials in wott-dashboard it would be automatically fetched by wott-agent daemon in two hours.
Or you can force it to be done immediately by restarting daemon with:

```Shell
$ systemctl restart wott-agent
```

or 

```Shell
$ service wott-agent restart
```   

## Usage

After wott-credentials was fetched to device you can select `WoTT` аuthentication method at settings page. 
Once enabled, any access to the web configuration interface (id. http://aaa.bbb.ccc.ddd:8080) will be asked for credentials (via browser UI).

## Notes

You can easily switch between `Basic auth` and `WoTT auth` methods through settings page, but if you want to disable authorisation 
you will be needed to empty `user` and `password` settings in `[auth_basic]` part and restart web server with:

```Shell
$ pkill -f server.py
```
   