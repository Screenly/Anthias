# HTTP basic authentication to restrict access to the web GUI

Access to Screenly web configuration interface can be restricted via an [*HTTP basic access authentication*](https://en.wikipedia.org/wiki/Basic_access_authentication) which denies access without proper login and password.

## Configuration

Login and password are defined in the `~/.screenly/screenly.conf` configuration file, in the `[auth]` part as shown below:

```
[auth]
; If desired, fill in with appropriate username and password.
user=
password=
```

By default, both fields are empty which disables the HTTP basic authentication leaving the web configuration interface accessible to anyone.

Modify the file to fill your desired login and passwords then restart the web server with:

```Shell
$ pkill -f server.py
```

## Missing `[auth]`

If the `[auth]` part is missing from your configuration file (which can occur if
you updated Screenly from a version that hadn't the HTTP basic authentication
feature), you can add it with this command (adapt for desired login and
password):

```Shell
$ cat >> ~/.screenly/screenly.conf <<'EOT'

[auth]
user = foo
password = bar
EOT
```

## Update command programmatically

To change both login and password with a single command, you can use:

```Shell
$ sed --in-place \
    -e 's/^user\s*=\s*.*/user = foo/' \
    -e 's/^password\s*=\s*.*/password = bar/' \
    ~/.screenly/screenly.conf
```

## Usage

Once enabled, any access to the web configuration interface (id. http://aaa.bbb.ccc.ddd:8080) will be asked for credentials (via browser UI).

## Notes

Sending credentials over nonencrypted channel such as HTTP (non-HTTPS) is discouraged. Please consider enabling SSL (see [this FAQ article](https://support.screenly.io/hc/en-us/articles/212107306-Does-Screenly-OSE-support-SSL-)) to protect your credentials from eavesdropping.
