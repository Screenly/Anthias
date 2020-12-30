This is a living document. It will be updated periodically. This is currently work-in-progress, and needs to be linked up with relevant issues.

# Near future

- [x] Make the [new browser](https://github.com/Screenly/screenly-ose-webview) the default browser (i.e. merge in the 'experimental branch').
- [x] Finalize the migration to Docker such that all workloads run in Docker.
- [ ] Patch up the new browser to support modern workloads (if necessary)
- [ ] Upgrade base image in [pi-gen](https://github.com/Screenly/pi-gen) to use Buster - [Pull Request](https://github.com/Screenly/pi-gen/pull/25)

## Known issues in master branch

- [x] Display/Monitor info under System Info is not working.
- [ ] Upgrade from web interface it not working (should probably be replaced with automatic upgrades).
- [ ] Backup feature is broken.
- [ ] [Balena WiFi Connect](https://www.balena.io/blog/resin-wifi-connect) for configuring WiFi needs to be restored.
- [ ] 'Updated Available' banner is showing permanently.
- [ ] System logs are not available due to inability to read systemd logs.
- [ ] System commands (reboot/shutdown) etc are not working. We need implement something similar to [Balena Supervisor](https://github.com/balena-io/balena-supervisor) (or ability to talk directly to systemd).

# Medium-term

- [ ] Migrate to Django and Python 3
  * It would be a good opportunity to begin the migration to Django while doing the migration to Python 3 anyways to avoid two rewrites
- [ ] Migrate API to [Django Rest Framework](https://www.django-rest-framework.org/)

# Long-term

- [x] Upgrade [new browser](https://github.com/Screenly/screenly-ose-webview) and add support for debugging
- [x] Add 4k support
