"""Shim for the stdlib ``cgi`` module, removed in Python 3.13 (Debian
trixie). Chromium 87 build tooling uses only ``cgi.escape`` (dropped from
the stdlib back in 3.8 in favour of ``html.escape``).
"""


def escape(s, quote=False):
    # Deliberately faithful to the ORIGINAL stdlib ``cgi.escape`` — with
    # quote=True it escapes only the double quote, NOT the single quote.
    # (That is what distinguished it from ``html.escape``, which escapes
    # both.) Matching cgi.escape exactly keeps the generated output
    # byte-identical to what Chromium 87's tooling produced on Python 2.
    s = s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    if quote:
        s = s.replace('"', '&quot;')
    return s
