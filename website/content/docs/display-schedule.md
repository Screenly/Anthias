---
title: "Display Schedule"
description: "Switch the screen off outside opening hours on a daily on/off schedule."
slug: "display-schedule"
aliases:
  - "/docs/display-schedule/"
---

A device can switch its screen off outside opening hours and back on in
the morning, on a daily schedule with per-weekday selection. The
schedule is **off by default**, so a device that has always stayed lit
keeps doing so after an upgrade.

Configure it under **Settings → Display schedule**.

## What actually happens at the scheduled time

Two things are sent on every transition, not one, because a device can
have a TV on one HDMI port and a plain monitor on another:

| Layer | Effect |
| ----- | ------ |
| HDMI-CEC | A TV that supports CEC genuinely powers down and back up. Sent to **every** HDMI port with a live CEC link, so a second attached display is not left lit. |
| Local blanking | The viewer blacks the screen out. On wayland boards this is a real DPMS off via `wlr-randr`; on the eglfs and linuxfb boards the viewer paints the screen black, because the Qt app holds DRM master and an external blank would be rejected. |

Most desktop monitors do not answer CEC at all, so on those the screen
goes black rather than powering down. Nothing needs configuring for
this: the schedule is not hidden or disabled on a device with no CEC
adapter.

## Field reference

| Field | Type | Default | Effect |
| ----- | ---- | ------- | ------ |
| `display_power_schedule_enabled` | bool | `false` | Master switch for the schedule |
| `display_power_on_time` | `HH:MM` | `08:00` | Time of day the display switches on |
| `display_power_off_time` | `HH:MM` | `18:00` | Time of day the display switches off |
| `display_power_days` | comma-separated ints `0`-`6` (Mon=0 … Sun=6) | `0,1,2,3,4,5,6` | Weekdays on which an on-period **begins** |

Times are wall-clock in the device's configured timezone (**Settings →
Timezone**), so they follow DST rather than drifting with it.

> **Note**
>
> The weekday numbering here is Monday=0 … Sunday=6, which is *not* the
> Monday=1 … Sunday=7 numbering used by [asset
> scheduling](/docs/asset-scheduling/)'s `play_days`. The two features
> were added at different times against different conventions; the web
> UI hides the difference behind the same day chips in both places.

## Days and overnight schedules

`display_power_days` selects the days an on-period *starts*. For a
schedule that runs past midnight, the small hours belong to the previous
day's on-period, so they are governed by the previous day's checkbox.

For example, on 18:00 → 06:00 with only Friday selected:

| Local time | Display | Why |
| ---------- | ------- | --- |
| Fri 17:30 | Off | Before the on-period |
| Fri 18:00 | On | On-period starts on Fri |
| Sat 02:00 | On | Still Friday's on-period |
| Sat 06:00 | Off | On-period ends |
| Sat 18:00 | Off | Sat is not selected |

A same-day schedule (on 08:00 → off 18:00) needs no special handling:
the whole on-period falls on the selected day.

If the on and off times are identical, the schedule describes neither an
on-period nor an off-period, and the device is left alone.

## Turning the schedule off

Disabling the schedule while the display is switched off restores it on
the next tick, rather than leaving a black screen with no way back. This
matters on a device with no CEC adapter, where the manual display-power
buttons are hidden entirely.

## Setting it through the API

The four fields are readable and writable on the v2 device-settings
endpoint, alongside the rest of the settings page:

```bash
$ curl http://<device-ip>/api/v2/device_settings

$ curl -X PATCH http://<device-ip>/api/v2/device_settings \
    -H 'Content-Type: application/json' \
    -d '{
          "display_power_schedule_enabled": true,
          "display_power_on_time": "07:30",
          "display_power_off_time": "22:00",
          "display_power_days": "0,1,2,3,4"
        }'
```

Unlike the web form, which keeps the previous value and shows a warning,
the API returns `400` for a malformed time or weekday: a client that
explicitly sent a field should be told the value was wrong rather than
have it reinterpreted. `HH:MM:SS` is accepted and normalized to `HH:MM`,
and an empty `display_power_days` normalizes to every day.

See the [API page](/api/) for the full endpoint reference.

## Manual control

**Settings → Display power** (marked *Experimental*) has "Turn display
on" / "Turn display off" buttons for a one-off change. That section is
CEC-only and appears only on devices that expose a CEC adapter, so it is
not a substitute for the schedule on a plain monitor.

A manual change is temporary while the schedule is enabled. The schedule
re-asserts the state it thinks the display should be in roughly every 10
minutes, so switching the screen on during an off-period buys about 10
minutes before it goes off again. That re-assertion is what makes the
feature survive a viewer restart: without it, a viewer that restarted at
22:00 would come back lit and stay lit until morning.

## Notes and limitations

- Transitions are applied within about 60 seconds of the configured time.
- Whether HDMI-CEC works at all depends on the display, the cable and
  the port. Many desktop monitors advertise no CEC in their EDID, and
  most x86 PCs have no CEC adapter. In that case the screen still goes
  black on schedule via local blanking; it just is not powered down.
- CEC is driven by the `anthias-viewer` container, which is the only
  one that can reach `/dev/cec*` on every board and every deployment. If
  the viewer is not running, display power reports an error.
- The **System Info** page has a "Display Power (CEC)" card showing what
  the device last read back over CEC. `No CEC display detected` is the
  normal reading for a plain monitor and is not a fault; `No CEC
  adapter` means the board exposes none. Only `CEC error` means
  something is actually wrong.

## Related documentation

- [Asset scheduling](/docs/asset-scheduling/): restrict individual assets
  to days and time windows.
- [API reference](/api/): set the schedule programmatically with the v2 API.
- [All documentation](/docs/): the full Anthias documentation index.
