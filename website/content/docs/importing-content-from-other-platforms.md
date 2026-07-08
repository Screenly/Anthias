---
title: "Importing Content from Other Platforms"
description: "Bring images, videos, and web pages into Anthias from another digital signage platform."
slug: "import-content"
aliases:
  - "/docs/importing-content-from-other-platforms/"
---

Moving to Anthias from another digital signage platform? Anthias ships a
built-in import wizard that copies your media across using the other
platform's API — no SSH or scripts required. Your existing Anthias assets
are left untouched, and re-running an import skips anything already brought
over, so it is safe to run more than once.

**Available now:** [Yodeck](https://www.yodeck.com/). Support for more
platforms (ScreenCloud, OptiSign, NoviSign, and PiSignage) is on the way.

## What gets imported

Anthias imports the content types it can play natively:

- **Images** and **videos** — the original file is downloaded onto your
  player and added to the schedule.
- **Web pages** — added as a web asset pointing at the same URL.

Anything Anthias cannot play, or that would not work outside the source
platform, is **skipped and reported** rather than imported half-way:

- **Audio** and **documents** (PDF, PowerPoint, and similar).
- **Apps and internally-generated content** — weather, clock, RSS, and
  dashboard widgets render inside the source platform, so their internal
  URLs would only produce broken assets on Anthias.
- **Files the platform does not expose for download** — if the source only
  offers a preview or thumbnail, the original cannot be copied; those items
  are flagged so you can re-upload them manually.

## Before you start

You will need an API token for the platform you are importing from. For
Yodeck:

1. Sign in to Yodeck.
2. Go to **Account Settings → Advanced Settings → API Tokens** and create a
   new token.
3. Keep the token handy. It is not stored on the device — it is only used to
   talk to the platform's API while the import runs.

## Run the import

1. Open your Anthias web interface and go to **Settings**.
2. In the **Import content** section, click **Import from Yodeck** (or the
   platform you are moving from).
3. Paste your API token and click **Continue**. Anthias validates the token
   before listing your media.
4. Review the list of media found. Supported items are selected by default;
   unsupported items are shown with the reason they will be skipped. Use
   **Select all** / **Select none** or the per-item checkboxes to adjust.
5. Leave **Enable imported assets** ticked to have them start playing right
   away, or untick it to import them disabled and enable them later.
6. Click **Import _N_ items**. Each selected item is copied in turn, with
   live per-item progress.
7. When it finishes, any items that failed can be retried with the **Retry
   _N_ failed** button, without repeating the ones that already succeeded.

That's it — your imported media now lives on your Anthias player, ready to
schedule like any other asset.

## Prefer the command line?

The same import runs headlessly for support and automation:

```bash
$ docker compose exec anthias-server \
    python manage.py import_content --provider yodeck --token '<your-token>'
```

Add `--dry-run` to list what would be imported without copying anything.
