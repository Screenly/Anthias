# Newsletter

`newsletter.mjml` is the release-announcement email, written in
[MJML](https://mjml.io/) and sent through Mailjet. It is the only thing
in here, and it is a marketing asset rather than anything a device
runs: no container builds it, no service serves it, and `bun run build`
does not touch it.

It lives in this repo anyway, next to `website/`, because it is built
out of the same design tokens as the management UI and the marketing
site. Kept anywhere else it would be a hand-matched approximation of
the product's colours, drifting a shade at a time.

## Building

```bash
bun run build:email     # -> emails/dist/newsletter.html (gitignored)
```

Paste the compiled HTML into a Mailjet template. The compiled file is
not committed: it is a build artifact of the `.mjml`, and two copies of
the same email is how a fix lands in one of them.

Check the size before sending. Gmail clips at 102 KB and shows a "view
entire message" link at the cut, which usually lands mid-newsletter.
The current template compiles to about 35 KB.

## Colours, sizes, and why they are all written out

Email clients strip CSS custom properties and Outlook drops `rgba()`,
so nothing from the token layer can be used here by name. Every value
arrives as a literal, which makes this file a second copy of the design
system that goes stale in silence. It already did once: the first
version was derived from `src/anthias_server/app/static/sass/_variables.scss`, and when the
authority moved into the `@theme` block in
`src/anthias_server/app/static/src/tailwind.css` the email kept painting the colours of a file
that no longer existed.

So the template carries one `RESOLVED TOKENS` table near the top, and
that is the only place a literal may be written.
`tests/test_email_tokens.py` re-derives every row from the real tokens,
rejects any literal further down that the table does not account for,
and fails on a row nothing uses. Change a colour in the design system
and the test tells you which row moved.

Alpha tokens get a backdrop named in the table, because a translucent
value has no literal until you know what is behind it. That is the
composite the test recomputes.

## Personalisation

Mailjet substitutes `{{var:first_name:"there"}}` and
`{{var:release_version:"2026.08.2"}}`; the fallback after the colon is
what a contact with no value for that field sees. `[[UNSUB_LINK_EN]]`
is Mailjet's unsubscribe shortcode and must stay in the footer, along
with the postal address, for CAN-SPAM.

## The masthead

`website/static/img/anthias-logo-email.png` exists for this email
alone. Gmail, Outlook and Yahoo all refuse to render SVG, so the site's
`logo-full.svg` cannot be used, and a transparent PNG picks up a black
matte in older Outlook builds, so it ships flattened onto the canvas
colour.

That puts a token inside a binary. To regenerate it after a change to
`--color-canvas` or to the logo itself:

```bash
uv run --no-project --with cairosvg python emails/render_logo.py
```

`cairosvg` is deliberately not a project dependency: it pulls the
native Cairo stack, and nothing else in the repo rasterises anything.

## Before sending

- Replace the body copy. What is in the file is the 2026.08.2
  announcement, kept real rather than filled with lorem ipsum so the
  template can be read as a finished email, and so the tone is on
  record: plain language, what changed and what to do about it.
- Update the release version, the release-notes link, and the two cards
  at the bottom.
- Send yourself a test. Gmail, Outlook and Apple Mail disagree about
  enough that a preview render is not a substitute.
