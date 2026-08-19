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
about 37 KB. CI enforces the limit, so a local build only needs checking
if you are curious how much room is left.

## Nothing may depend on the head

Every colour, size, weight and space is written on the element that uses
it. There is no `mj-attributes` block and no `mj-class` in the template,
and the tests fail the build if either comes back.

That rule was bought the hard way. The design used to live entirely in
one `mj-attributes` block: text colours, section backgrounds, every
padding. Strip that block, which is what happens the moment anything
takes the body without the head, and MJML falls back to its own
defaults, which is black text on the plum canvas at **1.6:1**, no cards,
and 20px of default padding everywhere. It still compiled, still sent,
and still looked like an email. That is what made it dangerous.

Three things hold the line now:

- `test_nothing_visual_depends_on_the_head` rejects an `mj-attributes`
  block or an `mj-class` reference.
- `test_every_styled_element_carries_its_own_styling` requires every
  `mj-text` and `mj-button` to set its own `color`, `font-family`,
  `font-size` and `line-height`. Deleting the block is only half of it,
  since an element that names no colour still inherits MJML's black.
- The build compiles the template a second time with `mj-head` deleted
  and fails on a single occurrence of MJML's fallback `#000000`. Only
  mjml can tell you whether the result still renders; the two tests
  above can only look for the constructs known to break it.

The head keeps the title, the preview text, the web font, and one media
query that adds a gap between the two cards when they stack. That query
is the one cosmetic thing allowed up there, and losing it costs the gap
and nothing else.

Link colours sit on each anchor rather than in `mj-style` for the same
reason, and because several clients drop a `<style>` block outright.

## Colours and sizes, and why they are all written out

Email clients strip CSS custom properties and Outlook drops `rgba()`,
so nothing from the token layer can be used here by name. Every value
arrives as a literal, which makes this file a second copy of the design
system that goes stale in silence. It already did once: the first
version was derived from
`src/anthias_server/app/static/sass/_variables.scss`, and when the
authority moved into the `@theme` block in
`src/anthias_server/app/static/src/tailwind.css` the email kept painting
the colours of a file that no longer existed.

So the template carries one `RESOLVED TOKENS` table near the top. That
is the only place a literal is written down as a *decision*; every use
site copies from it, which is what the repetition above is for.
`tests/test_email_tokens.py` re-derives every row from the real tokens,
rejects any literal that no row accounts for, and fails on a row nothing
uses. Change a colour in the design system and the test says which row
moved.

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
