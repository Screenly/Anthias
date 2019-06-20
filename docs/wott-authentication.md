# WoTT credentials based authentication to restrict access to the web GUI

Access to Screenly web configuration interface can be restricted via an [*HTTP basic access authentication*](https://en.wikipedia.org/wiki/Basic_access_authentication) which denies access without proper login and password. Using credentials provided through WoTT credentials system.


## Configuration

After installing [*wott-agent*](https://www.wott.io/) and claiming the device in [*wott-dashboard*](https://dash.wott.io).
Create credential with `name` `screenly_credentials` and `key` `login_credentials` contains colon separated username and password ( for example `pi:raspberry` ).
In `owner` field enter linux user under which Screenly-ose ran on your device ( f.ex. `pi`) 
If you want, you can change `name` for any you like, but it would be consist of lowercase unicode alphanuerics and `_.-:@` characters.
And you also need to set up this name in `~/.screenly/screenly.conf` configuration file, in the `[auth_wott]` part as shown below: 

```
[auth_wott]

wott_secret_name=screenly_credentials
```

After you set up this two credentials in wott-dashboard it would be automatically fetched by wott-agent daemon in 15 minutes.
Or you can force it to be done immediately by restarting daemon with:

```Shell
$ systemctl restart wott-agent
```

or 

```Shell
$ service wott-agent restart
```   

## Usage

After wott-credentials was fetched to device you can select `WoTT` authentication method at settings page. 
Once enabled, any access to the web configuration interface (id. http://aaa.bbb.ccc.ddd:8080) will be asked for credentials (via browser UI).

## Notes

WoTT credentials records update each 15 minutes. So if you made some changes in wott dashboard it may teake some time to be applied to your device.
So if you want it to be applied immediately you need to restart wott-agent.service, like described earlier in `Configuration` topic.