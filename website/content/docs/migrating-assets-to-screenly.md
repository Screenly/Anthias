---
title: "Migrating Assets to Screenly"
description: "Copy a player's assets from Anthias to a Screenly cloud account."
slug: "migrate-to-screenly"
aliases:
  - "/docs/migrating-assets-to-screenly/"
---

[Screenly](https://www.screenly.io/) is the commercial, cloud-managed
sibling of Anthias. If you want to move a player's assets up to a Screenly
account, Anthias ships a built-in migration wizard, no SSH or scripts
required. Existing assets already on Screenly are left untouched.

## Before you start

You'll need a Screenly API token:

1. **Sign up or sign in to Screenly** at
   [login.screenlyapp.com](https://login.screenlyapp.com/login).
2. In the Screenly dashboard, go to **Settings → Security → API Tokens**
   and create a new token.
3. Keep the token handy. It looks like `abcdef.123456…`. The token is not
   stored on the device. It is only used to talk to Screenly's API while
   the migration runs.

## Run the migration

1. Open your Anthias web interface and go to **Settings**.
2. In the **Migrate to Screenly** section, click **Start migration**.
3. On the **Get started** screen, review the steps and click **I have a
   token**.
4. Paste your Screenly API token and click **Continue**. Anthias validates
   the token against Screenly before moving on.
5. Choose which assets to migrate. All assets are selected by default; use
   **Select all** / **Select none** or the per-asset checkboxes to adjust.
6. Click the **Migrate _N_ assets** button. Each selected asset is uploaded
   to Screenly in turn, with live per-asset progress.
7. When it finishes, any assets that failed can be re-uploaded with the
   **Retry _N_ failed** button, without repeating the ones that already
   succeeded.

That's it. Your selected assets now live in your Screenly account.

## What gets migrated

The wizard copies the **media assets** stored on the device, images, videos,
and web-page URLs, up to your Screenly account. Each asset keeps its name and
its underlying file. What does **not** transfer automatically:

- **Schedules and playlists.** Screenly and Anthias model scheduling
  differently, so start/end times, durations, and playlist order are not carried
  over. You rebuild those in the Screenly dashboard once the assets have landed.
- **Device settings.** Network, SSL, and display configuration are specific to
  the Anthias host and stay on the device.
- **Assets already in Screenly.** The wizard skips anything it detects is
  already present, so re-running a migration will not create duplicates.

## Good to know

- The migration is **non-destructive**: your assets remain on the Anthias
  device after they are copied. Nothing is deleted locally.
- You can run the wizard as many times as you like, for example, to push a
  new batch of assets you added after the first run.
- Large videos take longer to upload; the per-asset progress bar shows which
  asset is in flight so you can leave it running unattended.

## Troubleshooting

- **"Invalid token."** Double-check that you copied the whole token (they look
  like `abcdef.123456…`) and that it has not been revoked in **Settings →
  Security → API Tokens** on Screenly.
- **An asset fails to upload.** Use the **Retry _N_ failed** button. It
  re-uploads only the assets that did not succeed, so you never re-send the ones
  that already made it across.
- **The device can't reach Screenly.** The migration talks to Screenly's API
  over HTTPS, so the device needs outbound internet access. Confirm the player
  is online before starting.

## Related documentation

- [Importing content from other platforms](/docs/import-content/): bring assets
  *into* Anthias from other digital-signage tools.
- [Asset scheduling](/docs/asset-scheduling/): how scheduling works on Anthias.
- [All documentation](/docs/): the full Anthias documentation index.
