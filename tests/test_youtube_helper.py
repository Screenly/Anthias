"""Unit tests for the URL-classification helper that gates the
YouTube download path. Substring-sniffing variants of this helper
were rejected during review because they either miss the short
``youtu.be`` form or false-positive on decoy hosts; these tests
encode both edges so a future regression to ``'youtube' in uri`` is
caught."""

import pytest

from anthias_common.youtube import is_youtube_url


# Sonar's S5332 (cleartext protocol) flags the http:// and ftp://
# fixtures below as security hotspots. They are intentional test
# data: the http://youtube.com/... case verifies the parser accepts
# legacy http YouTube URLs operators may have cached, and the
# ftp:// + plain-http negative cases assert the parser rejects
# unsupported schemes. None of these strings are dereferenced as
# URLs at test time. Same NOSONAR(S5332) suppression convention used
# elsewhere in the repo (see app/views._safe_redirect_uri).
@pytest.mark.parametrize(
    'uri',
    [
        'https://www.youtube.com/watch?v=abc',
        'https://youtube.com/watch?v=abc',
        'https://m.youtube.com/watch?v=abc',
        'https://music.youtube.com/watch?v=abc',
        'https://youtu.be/abc',
        'http://www.youtube.com/watch?v=abc',  # NOSONAR(S5332)
        'HTTPS://YouTube.com/watch?v=abc',
    ],
)
def test_is_youtube_url_recognises_canonical_forms(uri: str) -> None:
    assert is_youtube_url(uri) is True


@pytest.mark.parametrize(
    'uri',
    [
        '',
        'not a url',
        'http://example.com',  # NOSONAR(S5332)
        'https://evil.com/?youtube',
        'ftp://youtube.com/foo',  # NOSONAR(S5332)
        'https://attacker.youtube.com.evil.com/x',
        # Schemeless local paths must never trigger a download attempt.
        '/anthias_assets/abc.mp4',
    ],
)
def test_is_youtube_url_rejects_other_inputs(uri: str) -> None:
    assert is_youtube_url(uri) is False


def test_is_youtube_url_handles_whitespace() -> None:
    """Operators paste from clipboard; trailing whitespace shouldn't
    flip the classification."""
    assert is_youtube_url('  https://youtu.be/abc  ') is True
