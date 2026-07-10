"""Shim for the stdlib ``cgi`` module, removed in Python 3.13 (Debian
trixie). Chromium 87 build tooling uses only ``cgi.escape`` (dropped from
the stdlib back in 3.8 in favour of ``html.escape``).
"""


def escape(s, quote=False):
    s = s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    if quote:
        s = s.replace('"', '&quot;')
    return s
