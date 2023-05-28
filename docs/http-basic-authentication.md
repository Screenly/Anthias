# HTTP basic authentication to restrict access to the web GUI

Access to the web UI can be restricted via an [*HTTP basic access authentication*](https://en.wikipedia.org/wiki/Basic_access_authentication) which denies access without proper login and password.

## Configuration

Login and password are defined in the `~/.screenly/screenly.conf` configuration file, in the `[auth_basic]` part as shown below:

```
[auth_basic]
; Fill in with appropriate username and password
user=
password=
```

By default, both fields are empty which disables the HTTP basic authentication leaving the web configuration interface accessible to anyone.

Modify the file to fill your desired login and passwords then restart the web server with the following commands:

```Shell
$ cd /home/$USER/screenly
$ docker exec -it screenly-anthias-server-1 bash
$ pkill -f server.py
```

Alternatively, you can restart all the services with the following commands:

```Shell
$ cd /home/$USER/screenly/bin/upgrade_containers.sh
```

## Missing `[auth_basic]`

If the `[auth_basic]` part is missing from your configuration file (which can
occur if you updated Anthias from a version that doesn't have the HTTP basic
authentication feature), you can add it with this command (replace `foo` and
`bar` with your desired login and password):

```Shell
$ cat >> ~/.screenly/screenly.conf <<'EOT'

[auth]
user = foo
password = bar
EOT
```

Alternatively, you can change the username and password via `sed`:

```Shell
$ sed --in-place \
    -e 's/^user\s*=\s*.*/user = foo/' \
    -e 's/^password\s*=\s*.*/password = bar/' \
    ~/.screenly/screenly.conf
```

## Usage

Once enabled, any access to the web UI will require login.

## Notes

Sending credentials over nonencrypted channel such as HTTP (non-HTTPS) is discouraged. Please consider enabling SSL to protect your credentials from eavesdropping.
