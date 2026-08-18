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

## Getting it, if you are here to send it

You do not need a checkout. Every change to this directory runs
[Build Newsletter Email](../.github/workflows/build-email.yaml), which
attaches an `anthias-newsletter-<commit>.zip` to the run holding
`newsletter.mjml` and this file. Open the run from the commit or the
pull request and download it from the Artifacts section. The workflow
can also be started by hand against any branch from the Actions tab,
which is the quickest way to get the current template without pushing
anything.

What you get is the MJML source, not compiled HTML. Mailjet reads MJML
directly, so it is the file to paste, and it is the only version anyone
can still edit: nobody is going to change a headline inside 35 KB of
nested tables, which is how the sent mail and this repo would stop being
the same email.

Nothing else is in the bundle because nothing else is needed. The
masthead is referenced by URL from the site, so the template is already
the whole email.

That workflow is also where the compiled size gets checked. MJML expands
a template several times over, so a paragraph that reads as three lines
here can be a few KB of output, and the build fails rather than shipping
something Gmail will clip.

## Compiling it

```bash
bun run build:email     # -> emails/dist/newsletter.html
```

For looking at the result in a browser, and for the strict validation
pass. The compiled file is not committed and is not what gets sent:
it is an artifact of the `.mjml`, and two copies of one email is how a
fix lands in only one of them. `emails/dist/` needs no ignore entry of
its own, because the root `.gitignore` already ignores any directory
named `dist`.

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

It is referenced by URL rather than inlined into the template as a
base64 `data:` URI. Inlining would make the `.mjml` self-contained
offline, but the same three clients that refuse SVG also refuse `data:`
image URIs, which would put a broken image at the top of the newsletter
for most of the list. Apple Mail and Thunderbird would render it, which
is exactly enough to make the problem invisible in testing. The form of
embedding that does work everywhere is a `cid:` inline attachment, and
that is send-side setup in Mailjet rather than something the template
can carry.

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
