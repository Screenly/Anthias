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

## Getting the built email

If you are here to send it rather than to edit it, you do not need a
checkout. Every change to this directory runs
[Build Newsletter Email](../.github/workflows/build-email.yaml), which
attaches an `anthias-newsletter-<commit>.zip` to the run: the compiled
HTML, this README, and the masthead image. Open the run from the commit
or the pull request and download it from the Artifacts section. The
workflow can also be started by hand against any branch from the Actions
tab, which is the quickest way to get the current HTML without pushing
anything.

That workflow is also where the compiled size is checked. MJML expands a
template several times over, so a paragraph that reads as three lines in
the source can be a few KB of nested tables in the output, and the build
fails rather than shipping something Gmail will clip.

## Building it yourself

```bash
bun run build:email     # -> emails/dist/newsletter.html
```

Paste the compiled HTML into a Mailjet template. The compiled file is
not committed: it is a build artifact of the `.mjml`, and two copies of
the same email is how a fix lands in one of them. `emails/dist/` needs
no entry of its own, because the root `.gitignore` already ignores any
directory named `dist`.

Gmail clips at 102 KB and shows a "view entire message" link at the cut,
which usually lands mid-newsletter. The current template compiles to
about 35 KB. CI enforces the limit, so a local build only needs checking
if you are curious how much room is left.

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
