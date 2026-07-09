"""Unit tests for the per-asset custom-header helpers (feature #2215).

These cover the pure ``metadata['headers']`` sanitisers in
``anthias_server.app.models`` in isolation — no DB, no HTTP — so the
name/value rules that keep CR/LF off the wire are pinned independently of
the serializer and form layers that call them.
"""

import pytest

from anthias_server.app.models import (
    MAX_ASSET_HEADERS,
    normalize_asset_headers,
    parse_header_lines,
    validate_asset_headers,
)


class TestNormalizeAssetHeaders:
    def test_passes_valid_headers_through(self) -> None:
        headers = {'Authorization': 'Bearer abc', 'X-Env': 'prod'}
        assert normalize_asset_headers(headers) == headers

    @pytest.mark.parametrize(
        'value',
        [None, 'not-a-dict', 42, [], ('a', 'b')],
    )
    def test_non_dict_becomes_empty(self, value: object) -> None:
        assert normalize_asset_headers(value) == {}

    def test_drops_crlf_values(self) -> None:
        result = normalize_asset_headers(
            {'Good': 'ok', 'Bad': 'a\r\nEvil: 1', 'Also': 'b\nc'}
        )
        assert result == {'Good': 'ok'}

    def test_drops_null_byte_values(self) -> None:
        assert normalize_asset_headers({'X': 'a\x00b'}) == {}

    def test_drops_non_token_names(self) -> None:
        result = normalize_asset_headers(
            {'sp ace': 'v', 'co:lon': 'v', '': 'v', 'Ok': 'v'}
        )
        assert result == {'Ok': 'v'}

    def test_drops_non_string_entries(self) -> None:
        assert normalize_asset_headers({'X': 1, 2: 'v', 'Y': 'ok'}) == {
            'Y': 'ok'
        }

    def test_strips_whitespace_around_name(self) -> None:
        assert normalize_asset_headers({'  X-Env  ': 'prod'}) == {
            'X-Env': 'prod'
        }

    def test_caps_at_max(self) -> None:
        result = normalize_asset_headers(
            {f'X-H{i}': 'v' for i in range(MAX_ASSET_HEADERS + 5)}
        )
        assert len(result) == MAX_ASSET_HEADERS


class TestValidateAssetHeaders:
    def test_returns_clean_dict(self) -> None:
        headers = {'Authorization': 'Bearer x'}
        assert validate_asset_headers(headers) == headers

    @pytest.mark.parametrize(
        'value',
        [
            {'Bad Name': 'v'},
            {'X': 'a\r\nb'},
            {'': 'v'},
            {'X:': 'v'},
            {'X': 1},
            'not-a-dict',
        ],
    )
    def test_raises_on_malformed(self, value: object) -> None:
        with pytest.raises(ValueError):
            validate_asset_headers(value)

    def test_raises_over_count_cap(self) -> None:
        with pytest.raises(ValueError):
            validate_asset_headers(
                {f'X-H{i}': 'v' for i in range(MAX_ASSET_HEADERS + 1)}
            )


class TestParseHeaderLines:
    def test_parses_name_value_lines(self) -> None:
        text = 'Authorization: Bearer abc\nX-Env: prod'
        assert parse_header_lines(text) == {
            'Authorization': 'Bearer abc',
            'X-Env': 'prod',
        }

    def test_ignores_blank_and_colonless_lines(self) -> None:
        text = '\n\nAuthorization: x\njust-a-comment\n\n'
        assert parse_header_lines(text) == {'Authorization': 'x'}

    def test_value_keeps_later_colons(self) -> None:
        assert parse_header_lines('X-Range: bytes=0-1:2') == {
            'X-Range': 'bytes=0-1:2'
        }

    def test_drops_malformed_lines(self) -> None:
        # A CRLF can't survive splitlines(), but a bad name still drops.
        assert parse_header_lines('bad name: v\nOk: v') == {'Ok': 'v'}

    @pytest.mark.parametrize('value', [None, 42, []])
    def test_non_string_becomes_empty(self, value: object) -> None:
        assert parse_header_lines(value) == {}
