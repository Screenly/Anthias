# Release Notes

## Latest Changes

### Bug Fixes

* Fix the Anthias installer to work with Debian 12 (Bookworm).
* Remove the blinking cursor in the top-left corner of the display.
* Hide the Update Available header for Balena-based instances and for up-to-date
  Debian-based instances.
* Fix the Balena Supervisor version being displayed as *None* in the
  *Integrations* page.
* Fix the display showing HTTP 502 error when the web server is not yet ready.
* Fix shutdown and reboot via web not working after at least 10 minutes
  of uptime.
* Update the help text for the default durations in the *Settings* page.
* Fix URL validation when adding a new asset. Trigger the validation during
  keypress as well.
* Fix the splash screen not showing up-to-date IP addresses.
* Fix broken API docs page.

### Docs

* Update "What hardware to I need to run Anthas?" section in the website.
* Improve docs on using the Balena-based images.

### Enhancements

* Update the standby image to show the Anthias logo instead of the old
  Screenly logo.
* Include upgrade instructions in the Settings page that shows up if updates
  are available in Debian-based instances.

### New Features

* Add support for 4K display.
* Include IPv6 addresses in the splash screen.
* Includes support for installing an experimental version of Anthias on devices
  running Raspberry Pi OS Lite.

### Internal

* Remove unused image files.
* Remove unused USB assets, upgrade, and legacy Wi-Fi code.
* Fixes the Anthias to Screenly migration script.
* Introduces Python linting in CI.
* Upgrade the Docker containers from Buster to Bullseye. This includes the
  Python version bump from 3.7 to 3.11.
* Upgrade the WebView builder to use Bullseye instead of Buster.
* Use VLC as a replacement for OMXPlayer for video playback.
* Create a script for installing (trusted) self-signed certificates.

## v0.18.7

[Include changes that happened between v0.18.6 and v0.18.7]
