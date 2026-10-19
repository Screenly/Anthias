"""Tests for the connection-burst guards in ``bin/start_server.sh``.

An Anthias box answers on port 80 to anything that can route to it, and
a burst of concurrent connections used to walk the container's file
descriptor table to its 1024 ceiling — after which SQLite cannot open
the database, every request 500s, and the 500 handler cannot open
500.html either (Sentry ANTHIAS-62 / ANTHIAS-63). On the slowest board
on the testbed the server container died outright.

The guards live in a shell script rather than in Python, so they are
asserted here against the script text: the alternative is no coverage
at all, and a silent revert of either guard is exactly the regression
that would go unnoticed until a scanner next finds a device.
"""

import re
import subprocess
from pathlib import Path

import pytest

START_SERVER_SH = Path(__file__).resolve().parents[1] / 'bin/start_server.sh'

# The ceiling the guards exist to stay under: the stock soft limit a
# container gets when nothing sets one.
STOCK_SOFT_NOFILE = 1024

DEFAULT_LIMIT_CONCURRENCY = 256
RAISED_SOFT_NOFILE = 8192


@pytest.fixture(scope='module')
def script() -> str:
    return START_SERVER_SH.read_text()


def _eval_limit(env_value: str | None) -> str:
    """Resolve the script's concurrency default under a real shell.

    Re-implementing the parameter expansion in Python would test the
    re-implementation, so run the line the script actually carries.
    """
    line = next(
        ln
        for ln in START_SERVER_SH.read_text().splitlines()
        if ln.startswith('UVICORN_LIMIT_CONCURRENCY=')
    )
    env = {} if env_value is None else {'ANTHIAS_LIMIT_CONCURRENCY': env_value}
    return subprocess.run(
        ['bash', '-c', f'{line}\nprintf %s "$UVICORN_LIMIT_CONCURRENCY"'],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout


class TestConcurrencyLimit:
    def test_both_uvicorn_invocations_are_bounded(self, script: str) -> None:
        """The development branch matters as much as production.

        A developer running the dev stack on a routable address is as
        reachable as a fleet device, and the dev branch is the easy one
        to forget when editing the other.
        """
        execs = re.findall(
            r'exec uvicorn.*?(?=\n\w|\nfi|\Z)', script, re.DOTALL
        )

        assert len(execs) == 2, 'expected a dev and a production invocation'
        for invocation in execs:
            assert '--limit-concurrency' in invocation

    def test_defaults_to_a_value_well_under_the_stock_ceiling(self) -> None:
        assert _eval_limit(None) == str(DEFAULT_LIMIT_CONCURRENCY)
        assert DEFAULT_LIMIT_CONCURRENCY < STOCK_SOFT_NOFILE

    def test_operator_can_override(self) -> None:
        assert _eval_limit('64') == '64'


class TestDescriptorLimit:
    def test_soft_limit_is_raised(self, script: str) -> None:
        assert f'ulimit -n {RAISED_SOFT_NOFILE}' in script
        assert RAISED_SOFT_NOFILE > STOCK_SOFT_NOFILE

    def test_the_raise_cannot_abort_startup(self, script: str) -> None:
        """A kernel that refuses the raise must not stop the container.

        The concurrency bound is the guard that actually matters; the
        descriptor raise is headroom, and a headless device that will
        not boot is far worse than one without it.
        """
        raise_line = next(
            ln
            for ln in script.splitlines()
            if ln.startswith(f'ulimit -n {RAISED_SOFT_NOFILE}')
        )

        assert raise_line.endswith('|| true')

    def test_the_raise_precedes_the_server(self, script: str) -> None:
        assert script.index('ulimit -n') < script.index('exec uvicorn')
