from unittest import mock

from anthias_common.http import (
    ANTHIAS_HOMEPAGE,
    get_anthias_product_token,
    get_user_agent,
)


def test_product_token_uses_release() -> None:
    with mock.patch(
        'anthias_common.http.get_anthias_release', return_value='2026.6.3'
    ):
        assert get_anthias_product_token() == 'Anthias/2026.6.3'


def test_product_token_falls_back_to_unknown() -> None:
    # get_anthias_release() returns '' when neither the installed package
    # metadata nor the repo-root pyproject.toml can be read.
    with mock.patch(
        'anthias_common.http.get_anthias_release', return_value=''
    ):
        assert get_anthias_product_token() == 'Anthias/unknown'


def test_user_agent_wraps_product_token_with_homepage() -> None:
    with mock.patch(
        'anthias_common.http.get_anthias_release', return_value='2026.6.3'
    ):
        assert get_user_agent() == (f'Anthias/2026.6.3 (+{ANTHIAS_HOMEPAGE})')
