---
title: "Importing Content from Other Platforms"
description: "Bring images, videos, and web pages into Anthias from another digital signage platform."
slug: "import-content"
aliases:
  - "/docs/importing-content-from-other-platforms/"
---

Moving to Anthias from another digital signage platform? Anthias ships a
built-in import wizard that copies your media across using the other
platform's API, so no SSH or scripts are required. Your existing Anthias
assets are left untouched, and re-running an import skips anything already
brought over, so it is safe to run more than once.

**Supported platforms:** Yodeck, ScreenCloud, piSignage, and Xibo (Xibo
Cloud or self-hosted).

## What gets imported

Anthias imports the content types it can play natively:

- **Images** and **videos**: the file is downloaded onto your player and
  added to the schedule (the original where the platform exposes it, or the
  best available rendition otherwise).
- **Web pages**: added as a web asset pointing at the same URL.

Anything Anthias cannot play, or that would not work outside the source
platform, is **skipped and reported** rather than imported half-way:

- **Audio** and **documents** (PDF, PowerPoint, and similar).
- **Apps and internally-generated content**: weather, clock, RSS, and
  dashboard widgets render inside the source platform, so their internal
  URLs would only produce broken assets on Anthias.
- **Files with no downloadable URL**: when the platform exposes neither the
  original nor a usable rendition, the item is flagged for you to re-upload
  manually.

## Before you start

You will need API credentials for the platform you are importing from. What
to enter differs slightly per platform, and the wizard shows a reminder for
each. Credentials are **not** stored on the device; they are only used to
talk to the platform's API while the import runs.

| Platform | What to enter in the wizard | Where to create it |
| --- | --- | --- |
| **Yodeck** | Your API token | Account Settings → Advanced Settings → API Tokens |
| **ScreenCloud** | Your API token (the region is detected automatically) | Studio → Account Settings → Developer → New Token |
| **piSignage** | `subdomain:email:password` (the `<name>` in `<name>.pisignage.com`, plus your login) | Your piSignage account login |
| **Xibo** | `cms-url client_id client_secret` (space separated; the CMS URL works for Xibo Cloud or self-hosted) | Applications → Add (an API application) |

## Run the import

1. Open your Anthias web interface and go to **Settings**.
2. In the **Import content** section, click **Import from _&lt;platform&gt;_** for
   the platform you are moving from.
3. Paste your credentials and click **Continue**. Anthias validates them
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

That is it. Your imported media now lives on your Anthias player, ready to
schedule like any other asset.

## Prefer the command line?

The same import runs headlessly for support and automation:

```bash
$ docker compose exec anthias-server \
    python manage.py import_content --provider yodeck --token '<credentials>'
```

Use `--provider` with one of `yodeck`, `screencloud`, `pisignage`, or
`xibo`, and pass the same credentials you would enter in the wizard. Add
`--dry-run` to list what would be imported without copying anything.
