# Asset scheduling

Each asset can be restricted to a specific set of days of the week and a
daily time window, on top of the existing `start_date` / `end_date` range.
With the defaults, scheduling is a no-op and assets behave as before.

## Field reference

| Field | Type | Default | Effect |
| ----- | ---- | ------- | ------ |
| `play_days` | JSON list of ints `1`-`7` (Mon-Sun, ISO weekday) | `[1,2,3,4,5,6,7]` | Days of week the asset is eligible to play |
| `play_time_from` | `time` (HH:MM:SS) | `null` | Start of daily play window. `null` together with `play_time_to=null` means "anytime" |
| `play_time_to` | `time` (HH:MM:SS) | `null` | End of daily play window (exclusive). `null` together with `play_time_from=null` means "anytime" |

`is_enabled`, `start_date`, `end_date`, `play_order`, `duration`, and
`skip_asset_check` keep their existing meanings.

## When is an asset active?

An asset is active (and therefore eligible for the playlist) only when
**all** of the following are true:

| Check | Active when... |
| ----- | -------------- |
| Enabled | `is_enabled = true` |
| Date range | `start_date < now < end_date` (both must be set) |
| Day of week | local weekday is in `play_days` |
| Time of day | local time is in `[play_time_from, play_time_to)`, or both fields are `null` |

Time-of-day and day-of-week checks use the device's local timezone.

## Common patterns

| Goal | `start_date` / `end_date` | `play_days` | `play_time_from` | `play_time_to` |
| ---- | -------------------------- | ----------- | ---------------- | -------------- |
| Always (legacy behavior) | covering the desired range | `[1,2,3,4,5,6,7]` | `null` | `null` |
| Weekdays only | covering the desired range | `[1,2,3,4,5]` | `null` | `null` |
| Weekday business hours | covering the desired range | `[1,2,3,4,5]` | `09:00:00` | `17:00:00` |
| Weekend evenings | covering the desired range | `[6,7]` | `18:00:00` | `23:00:00` |
| Lunchtime menu | covering the desired range | `[1,2,3,4,5,6,7]` | `11:30:00` | `14:00:00` |
| Friday late-night promo | covering the desired range | `[5]` | `22:00:00` | `02:00:00` |
| Single-day takeover | `2026-11-27` / `2026-11-28` | `[1,2,3,4,5,6,7]` | `null` | `null` |
| Mon morning rotation, 5 weeks | range that covers 5 Mondays | `[1]` | `09:00:00` | `12:00:00` |

## Overnight windows

When `play_time_from > play_time_to`, the window wraps past midnight.
`play_days` refers to the **start** day of the window.

For example, a slot of `play_days=[1]` (Mon) with `22:00 → 06:00`:

| Local time | Active? | Why |
| ---------- | ------- | --- |
| Mon 21:30 | No | Before window |
| Mon 22:00 | Yes | Window starts on Mon |
| Mon 23:30 | Yes | Pre-midnight portion of Mon's window |
| Tue 02:30 | Yes | Post-midnight portion; "yesterday" was Mon (in days) |
| Tue 06:00 | No | Window ends (exclusive) |
| Tue 22:00 | No | Tue not in `play_days` |
| Wed 02:30 | No | "Yesterday" was Tue (not in days) |

## Notes

- `play_time_to` is **exclusive**: `09:00:00 → 17:00:00` covers up to but
  not including 17:00:00. Use `17:00:01` or similar if you need to
  include the boundary minute.
- The viewer re-evaluates the playlist when an asset's window opens or
  closes; transitions are picked up within ~60 seconds.
- DST and other clock changes use wall-clock semantics: a window
  configured for the spring-forward gap (02:00–03:00 on the affected
  day) simply will not fire that day; a window during the fall-back
  hour will fire twice.
- v1.x of the REST API does not expose these fields; use v2 to set
  them. Existing v1.x clients see no change in behavior.
