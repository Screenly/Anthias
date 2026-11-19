"""Confine file paths to a directory.

Anything that builds a path from a request, a header, a stored asset
row or a restored backup and then opens, renames or deletes it goes
through here. A prefix test on the raw string is not enough:
``<assetdir>/../.anthias/anthias.db`` starts with ``<assetdir>`` and
names a file outside it, and a symlink inside the directory can point
anywhere.

Two checks, because each catches what the other cannot:

* the normalised path must start with the normalised base plus a
  separator, which rejects ``..`` and absolute parts lexically, and
  rules out a sibling such as ``<assetdir>-old``;
* the ``realpath`` of the result must start with the ``realpath`` of
  the base, which rejects a symlink that leads back out.

``resolve_within`` returns the *normalised* path, not the resolved
one. Asset rows store paths under the configured asset dir, and on an
install that reaches that dir through a symlink (pre-rebrand devices
keep back-compat links) the resolved form would read as a different
directory to every string comparison elsewhere.
"""

from os import path


class PathOutsideBaseError(ValueError):
    """A path that would land outside the directory it must stay in."""


def is_within(base: str, candidate: str) -> bool:
    """True when ``candidate`` is strictly inside ``base``.

    Checked both lexically and after resolving symlinks.
    """
    normalised = path.normpath(candidate)
    if not normalised.startswith(path.join(path.normpath(base), '')):
        return False
    resolved = path.realpath(normalised)
    return resolved.startswith(path.join(path.realpath(base), ''))


def resolve_within(base: str, *parts: str) -> str:
    """Join ``parts`` onto ``base``, refusing anything that escapes it.

    Returns the normalised joined path. Raises ``PathOutsideBaseError``
    if it is not strictly inside ``base``, whether through ``..``, an
    absolute part, or a symlink.
    """
    joined = path.normpath(path.join(base, *parts))
    if not is_within(base, joined):
        raise PathOutsideBaseError(joined)
    return joined
