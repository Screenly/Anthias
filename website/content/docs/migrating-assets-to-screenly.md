---
title: "Migrating Assets to Screenly"
description: "Copy a player's assets from Anthias to a Screenly cloud account."
slug: "migrate-to-screenly"
aliases:
  - "/docs/migrating-assets-to-screenly/"
---

[Screenly](https://www.screenly.io/) is the commercial, cloud-managed
sibling of Anthias. If you want to move a player's assets up to a Screenly
account, Anthias ships a built-in migration wizard — no SSH or scripts
required. Existing assets already on Screenly are left untouched.

## Before you start

You'll need a Screenly API token:

1. **Sign up or sign in to Screenly** at
   [login.screenlyapp.com](https://login.screenlyapp.com/login).
2. In the Screenly dashboard, go to **Settings → Security → API Tokens**
   and create a new token.
3. Keep the token handy. It looks like `abcdef.123456…`. The token is not
   stored on the device — it is only used to talk to Screenly's API while
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

That's it — your selected assets now live in your Screenly account.
