"""Tests for the host system-log snapshot path.

Anthias had no way to read the host journal from inside the
container: ``bin/collect_debug.sh`` shells out to ``journalctl`` on
bare metal, but nothing in the app or API could. ``/api/v2/system/logs``
closes that, behind one contract across two very different backends —
the Balena supervisor's ``POST /v2/journal-logs``, and the
``anthias-host-agent`` systemd unit reached over Redis.

What's pinned here:

* the shared wire contract (caps, argv construction, normalisation,
  redaction) in ``anthias_common.journal``, which both sides import so
  they cannot drift;
* the host agent's JSON ``hostcmd`` branch — including that the
  original bare-string commands still work, and that every failure
  path still writes exactly one reply so the container's blocking pop
  resolves instead of timing out;
* that nothing in the journal path can block ``subscriber_loop``
  indefinitely (line cap, byte cap, subprocess timeout);
* both server-side backends, including their failure modes.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any
from unittest import mock

import pytest
import redis
import requests

from anthias_common import journal
from anthias_server.lib import system_logs

HOST_AGENT = 'anthias_host_agent.__main__'
SYSTEM_LOGS = 'anthias_server.lib.system_logs'


def _record(**overrides: Any) -> dict[str, Any]:
    record = {
        '__REALTIME_TIMESTAMP': '1761465600000000',
        'PRIORITY': '6',
        '_SYSTEMD_UNIT': 'docker.service',
        'SYSLOG_IDENTIFIER': 'dockerd',
        '_HOSTNAME': 'anthias',
        'MESSAGE': 'hello',
    }
    record.update(overrides)
    return record


def _payload(*records: dict[str, Any]) -> str:
    return '\n'.join(json.dumps(record) for record in records) + '\n'


# ---------------------------------------------------------------------
# Shared contract
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ('requested', 'expected'),
    [
        (None, journal.DEFAULT_COUNT),
        ('not-a-number', journal.DEFAULT_COUNT),
        (0, 1),
        (-5, 1),
        (10, 10),
        (journal.MAX_COUNT + 1, journal.MAX_COUNT),
        (10**9, journal.MAX_COUNT),
    ],
)
def test_clamp_count(requested: Any, expected: int) -> None:
    assert journal.clamp_count(requested) == expected


def test_journalctl_argv_uses_equals_form_so_values_cannot_become_flags() -> (
    None
):
    argv = journal.build_journalctl_argv(
        count=50,
        unit='docker.service',
        since='-1h',
        until='now',
        container='anthias-anthias-server-1',
    )
    assert argv[0] == 'journalctl'
    assert '--lines=50' in argv
    assert '--unit=docker.service' in argv
    # '-1h' is a legal journalctl time spec that also looks like a
    # flag. The '--opt=value' form is what keeps it a value.
    assert '--since=-1h' in argv
    assert '--until=now' in argv
    # The match expression has to come last or it is consumed as an
    # option argument.
    assert argv[-1] == 'CONTAINER_NAME=anthias-anthias-server-1'
    assert not any(arg == '-1h' for arg in argv)


def test_journalctl_argv_omits_unset_filters() -> None:
    argv = journal.build_journalctl_argv(count=5)
    assert argv == [
        'journalctl',
        '--no-pager',
        '--output=json',
        '--all',
        '--lines=5',
    ]


@pytest.mark.parametrize(
    'kwargs',
    [
        {'unit': 'docker.service; rm -rf /'},
        {'unit': '--follow'},
        {'container': '-n'},
        {'container': '../../etc/passwd'},
        {'since': '$(id)'},
        {'until': '`id`'},
        {'since': 'x' * 100},
    ],
)
def test_hostile_filters_are_rejected(kwargs: dict[str, str]) -> None:
    with pytest.raises(journal.JournalRequestError):
        journal.build_journalctl_argv(count=10, **kwargs)


def test_normalize_payload_maps_journald_json_to_the_wire_shape() -> None:
    entries = journal.normalize_payload(
        _payload(_record(), _record(CONTAINER_NAME='anthias-redis-1'))
    )
    assert entries[0] == {
        'timestamp': '2025-10-26T08:00:00Z',
        'priority': 6,
        'unit': 'docker.service',
        'identifier': 'dockerd',
        'hostname': 'anthias',
        'container_name': None,
        'message': 'hello',
    }
    assert entries[1]['container_name'] == 'anthias-redis-1'


def test_normalize_payload_drops_a_truncated_trailing_line() -> None:
    # The byte cap can cut the last record mid-object. Losing one
    # entry at the boundary beats failing the whole snapshot.
    truncated = _payload(_record()) + '{"MESSAGE": "half a reco'
    entries = journal.normalize_payload(truncated)
    assert len(entries) == 1
    assert entries[0]['message'] == 'hello'


def test_normalize_payload_decodes_non_utf8_messages() -> None:
    # journald emits a non-UTF-8 MESSAGE as an array of byte values,
    # not a string. The wire type has to stay 'string' either way.
    entries = journal.normalize_payload(
        _payload(_record(MESSAGE=[104, 105, 255]))
    )
    assert entries[0]['message'].startswith('hi')


def test_normalize_payload_tolerates_missing_fields() -> None:
    entries = journal.normalize_payload('{}\n')
    assert entries == [
        {
            'timestamp': None,
            'priority': None,
            'unit': None,
            'identifier': None,
            'hostname': None,
            'container_name': None,
            'message': '',
        }
    ]


@pytest.mark.parametrize(
    ('raw', 'must_not_contain'),
    [
        ('psk=hunter2supersecret', 'hunter2supersecret'),
        ('wpa_passphrase="correct horse battery"', 'correct horse battery'),
        ('PASSWORD: s3cr3t-value', 's3cr3t-value'),
        ('api_key=abcd1234efgh', 'abcd1234efgh'),
        ('Authorization: Bearer eyJhbGciOi', 'eyJhbGciOi'),
    ],
)
def test_redaction_blanks_secret_values(
    raw: str, must_not_contain: str
) -> None:
    redacted = journal.redact(raw)
    assert must_not_contain not in redacted
    assert journal.REDACTED in redacted


def test_redaction_keeps_the_key_visible() -> None:
    # A redacted line still has to say what happened, otherwise it is
    # useless for the debugging it exists to support.
    assert journal.redact('psk=topsecret').startswith('psk')


def test_redaction_leaves_ordinary_lines_alone() -> None:
    line = 'anthias-viewer: playing asset 42 for 20s'
    assert journal.redact(line) == line


def test_redaction_is_applied_by_the_normaliser() -> None:
    entries = journal.normalize_payload(
        _payload(_record(MESSAGE='connecting with psk=letmein'))
    )
    assert 'letmein' not in entries[0]['message']


@pytest.mark.parametrize(
    'line',
    [
        # A long run of word characters with no separator: the shape
        # that made the key=value pattern backtrack.
        'a' * 49152,
        '_' * 49152,
        # Runs that repeatedly ALMOST match a secret keyword.
        'psk_' * 12288,
        'token' * 9830,
        'a=b ' * 12288,
    ],
)
def test_redaction_is_linear_not_quadratic(line: str) -> None:
    # journald's default LineMax is 48 KB, so a single line this size
    # is something a host can really emit. Before the lookbehind was
    # added the key=value pattern was quadratic -- measured at 6.3s
    # for 16 KB, extrapolating to roughly a minute for 48 KB, with a
    # request worker pinned for the duration.
    #
    # The bound is deliberately loose (a slow shared CI runner should
    # not flake this); the regression it catches is three orders of
    # magnitude away from it.
    started = time.perf_counter()
    journal.redact(line)
    assert time.perf_counter() - started < 1.0


# ---------------------------------------------------------------------
# Host agent (bare metal)
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    'payload',
    ['reboot', 'shutdown', 'set_ip_addresses', 'set_ip_addresses_fast'],
)
def test_bare_string_commands_still_parse(payload: str) -> None:
    # Back-compat during rollout: a container running an older image
    # than the host agent publishes bare strings, and must keep
    # working.
    from anthias_host_agent.__main__ import parse_host_command

    assert parse_host_command(payload) == (payload, None, {})


def test_json_commands_carry_a_correlation_id_and_args() -> None:
    from anthias_host_agent.__main__ import parse_host_command

    request = journal.build_request('abc123', count=50, unit='docker.service')
    cmd, request_id, args = parse_host_command(request)
    assert cmd == journal.JOURNAL_LOGS_COMMAND
    assert request_id == 'abc123'
    assert args == {'count': 50, 'unit': 'docker.service'}


def test_malformed_json_falls_back_to_the_bare_string_path() -> None:
    # Must not raise out of the pubsub handler — an exception there
    # ends subscriber_loop and takes reboot handling with it.
    from anthias_host_agent.__main__ import parse_host_command

    cmd, request_id, args = parse_host_command('{not json')
    assert cmd == '{not json'
    assert request_id is None
    assert args == {}


def test_unparseable_command_is_logged_not_raised() -> None:
    from anthias_host_agent.__main__ import execute_host_command

    # No exception, no dispatch.
    execute_host_command('{not json')


@pytest.fixture
def fake_rdb() -> mock.MagicMock:
    return mock.MagicMock()


def _run_journal_command(
    fake_rdb: mock.MagicMock,
    *,
    request: str,
    run_result: Any = None,
    run_side_effect: Any = None,
    persistent: bool = True,
) -> list[dict[str, Any]]:
    """Dispatch one hostcmd payload and return the replies pushed."""
    from anthias_host_agent.__main__ import execute_host_command

    run_kwargs: dict[str, Any] = {}
    if run_side_effect is not None:
        run_kwargs['side_effect'] = run_side_effect
    else:
        run_kwargs['return_value'] = run_result

    with (
        mock.patch(f'{HOST_AGENT}.redis.Redis', return_value=fake_rdb),
        mock.patch(f'{HOST_AGENT}._run_journalctl', **run_kwargs),
        mock.patch(
            f'{HOST_AGENT}.glob.glob',
            side_effect=lambda pattern: (
                ['/var/log/journal/abc/system.journal']
                if pattern == journal.PERSISTENT_JOURNAL_GLOB and persistent
                else []
            ),
        ),
        mock.patch(f'{HOST_AGENT}.os.path.getmtime', return_value=1.0),
        mock.patch(f'{HOST_AGENT}.os.access', return_value=True),
    ):
        execute_host_command(request)

    return [json.loads(call.args[1]) for call in fake_rdb.rpush.call_args_list]


def test_host_agent_replies_with_a_snapshot(
    fake_rdb: mock.MagicMock,
) -> None:
    completed = mock.Mock(returncode=0, stdout=_payload(_record()), stderr='')
    replies = _run_journal_command(
        fake_rdb,
        request=journal.build_request('req-1', count=10),
        run_result=completed,
    )

    assert len(replies) == 1
    assert replies[0]['ok'] is True
    assert replies[0]['truncated'] is False
    assert replies[0]['persistent'] is True
    assert journal.normalize_payload(replies[0]['payload'])[0]['message'] == (
        'hello'
    )
    fake_rdb.rpush.assert_called_once()
    assert fake_rdb.rpush.call_args.args[0] == journal.result_key('req-1')


def test_host_agent_sets_a_ttl_on_every_reply(
    fake_rdb: mock.MagicMock,
) -> None:
    # Without the TTL, a reply nobody pops (the requester already gave
    # up) would sit in Redis holding journal text forever.
    completed = mock.Mock(returncode=0, stdout='', stderr='')
    _run_journal_command(
        fake_rdb,
        request=journal.build_request('req-2'),
        run_result=completed,
    )
    fake_rdb.expire.assert_called_once_with(
        journal.result_key('req-2'), journal.RESULT_TTL_SECONDS
    )


def test_host_agent_bounds_the_subprocess(fake_rdb: mock.MagicMock) -> None:
    # The hard constraint: subscriber_loop is single-threaded, so a
    # wedged journalctl must not pin it. This is also why --follow is
    # not offered on either platform.
    completed = mock.Mock(returncode=0, stdout='', stderr='')
    from anthias_host_agent.__main__ import execute_host_command

    with (
        mock.patch(f'{HOST_AGENT}.redis.Redis', return_value=fake_rdb),
        mock.patch(
            f'{HOST_AGENT}._run_journalctl', return_value=completed
        ) as run,
        mock.patch(
            f'{HOST_AGENT}.glob.glob',
            side_effect=lambda pattern: (
                ['x.journal']
                if pattern == journal.PERSISTENT_JOURNAL_GLOB
                else []
            ),
        ),
        mock.patch(f'{HOST_AGENT}.os.path.getmtime', return_value=1.0),
        mock.patch(f'{HOST_AGENT}.os.access', return_value=True),
    ):
        execute_host_command(journal.build_request('req-3'))

    assert '--all' in run.call_args.args[0]
    assert '--lines=500' in run.call_args.args[0]
    assert not any('follow' in arg for arg in run.call_args.args[0])


def test_journalctl_output_is_decoded_leniently() -> None:
    from anthias_host_agent.__main__ import _run_journalctl

    completed = _run_journalctl(
        [
            sys.executable,
            '-c',
            "import os; os.write(1, b'caf\\xc3\\xa9\\xff'); os.write(2, b'\\xff')",
        ]
    )

    assert completed.returncode == 0
    assert completed.stdout == 'café\ufffd'
    assert completed.stderr == '\ufffd'


def test_journalctl_drains_both_pipes_and_keeps_a_bounded_tail() -> None:
    from anthias_host_agent.__main__ import _run_journalctl

    # Exceed both pipe capacity and the payload cap; both pipes must be
    # drained even after their retained buffers fill, without deadlock.
    completed = _run_journalctl(
        [
            sys.executable,
            '-c',
            (
                f"import os; os.write(1, b'x' * {journal.MAX_PAYLOAD_BYTES * 2}); "
                "os.write(2, b'e' * 100000); os.write(1, b'newest')"
            ),
        ]
    )

    assert completed.returncode == 0
    assert len(completed.stdout) == journal.MAX_PAYLOAD_BYTES + 1
    assert completed.stdout.endswith('newest')
    assert completed.stderr == 'e' * 500


@pytest.mark.parametrize('close_pipes', [False, True])
def test_journalctl_times_out_and_reaps_the_child(
    monkeypatch: pytest.MonkeyPatch,
    close_pipes: bool,
) -> None:
    from anthias_host_agent import __main__ as host_agent

    monkeypatch.setattr(host_agent, 'SUBPROCESS_TIMEOUT_SECONDS', 0.2)
    children = []
    popen = subprocess.Popen

    def spawn(*args: Any, **kwargs: Any) -> Any:
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    # Patch the stdlib module, not `host_agent.subprocess`. It is the
    # same module object either way -- _run_journalctl resolves
    # `subprocess.Popen` through its own globals -- but reaching for it
    # as an attribute of the host-agent module is an implicit
    # re-export, which mypy rejects under no_implicit_reexport.
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    script = 'import os, time; '
    if close_pipes:
        script += 'os.close(1); os.close(2); '
    script += 'time.sleep(30)'
    with pytest.raises(subprocess.TimeoutExpired):
        host_agent._run_journalctl([sys.executable, '-c', script])

    assert len(children) == 1
    assert children[0].returncode is not None


def test_journalctl_preserves_messages_over_4096_bytes() -> None:
    from anthias_host_agent.__main__ import _run_journalctl

    # Simulate journalctl's documented JSON behavior with and without
    # --all, using the actual argv builder and subprocess reader.
    script = (
        'import json, sys; '
        "print(json.dumps({'MESSAGE': "
        "'long-message ' * 500 if '--all' in sys.argv else None}))"
    )
    argv = journal.build_journalctl_argv(count=1)
    completed = _run_journalctl([sys.executable, '-c', script, *argv[1:]])
    entries = journal.normalize_payload(completed.stdout)
    assert entries[0]['message'] == 'long-message ' * 500


def test_host_agent_caps_a_huge_payload(fake_rdb: mock.MagicMock) -> None:
    oversized = 'x' * (journal.MAX_PAYLOAD_BYTES + 1024)
    completed = mock.Mock(returncode=0, stdout=oversized, stderr='')
    replies = _run_journal_command(
        fake_rdb,
        request=journal.build_request('req-4'),
        run_result=completed,
    )
    assert replies[0]['truncated'] is True
    assert len(replies[0]['payload'].encode()) <= journal.MAX_PAYLOAD_BYTES


def test_host_agent_clamps_a_count_from_the_wire(
    fake_rdb: mock.MagicMock,
) -> None:
    # Re-clamped on the host because hostcmd is unauthenticated local
    # Redis — the API serializer's cap is not the only gate.
    completed = mock.Mock(returncode=0, stdout='', stderr='')
    from anthias_host_agent.__main__ import execute_host_command

    hostile = json.dumps(
        {
            'cmd': journal.JOURNAL_LOGS_COMMAND,
            'id': 'req-5',
            'args': {'count': 10**9},
        }
    )
    with (
        mock.patch(f'{HOST_AGENT}.redis.Redis', return_value=fake_rdb),
        mock.patch(
            f'{HOST_AGENT}._run_journalctl', return_value=completed
        ) as run,
        mock.patch(
            f'{HOST_AGENT}.glob.glob',
            side_effect=lambda pattern: (
                ['x.journal']
                if pattern == journal.PERSISTENT_JOURNAL_GLOB
                else []
            ),
        ),
        mock.patch(f'{HOST_AGENT}.os.path.getmtime', return_value=1.0),
        mock.patch(f'{HOST_AGENT}.os.access', return_value=True),
    ):
        execute_host_command(hostile)

    assert f'--lines={journal.MAX_COUNT}' in run.call_args.args[0]


def test_host_agent_rejects_a_hostile_filter_from_the_wire(
    fake_rdb: mock.MagicMock,
) -> None:
    from anthias_host_agent.__main__ import execute_host_command

    hostile = json.dumps(
        {
            'cmd': journal.JOURNAL_LOGS_COMMAND,
            'id': 'req-6',
            'args': {'unit': '--follow'},
        }
    )
    with (
        mock.patch(f'{HOST_AGENT}.redis.Redis', return_value=fake_rdb),
        mock.patch(f'{HOST_AGENT}._run_journalctl') as run,
    ):
        execute_host_command(hostile)

    run.assert_not_called()
    reply = json.loads(fake_rdb.rpush.call_args.args[1])
    assert reply['ok'] is False


@pytest.mark.parametrize(
    ('kwargs', 'expected_fragment'),
    [
        (
            {
                'run_side_effect': __import__('subprocess').TimeoutExpired(
                    'journalctl', 20
                )
            },
            'Timed out',
        ),
        ({'run_side_effect': OSError('no such file')}, 'not available'),
        (
            {
                'run_result': mock.Mock(
                    returncode=1, stdout='', stderr='Failed to open journal'
                )
            },
            'Failed to open journal',
        ),
    ],
)
def test_every_host_agent_failure_still_replies(
    fake_rdb: mock.MagicMock,
    kwargs: dict[str, Any],
    expected_fragment: str,
) -> None:
    # If a failure path returned without replying, the container's
    # BLPOP would sit out its full timeout for a request that already
    # failed.
    replies = _run_journal_command(
        fake_rdb, request=journal.build_request('req-7'), **kwargs
    )
    assert len(replies) == 1
    assert replies[0]['ok'] is False
    assert expected_fragment in replies[0]['error']


@pytest.mark.parametrize(
    ('files', 'readable', 'expected_ok'),
    [
        # The pre-fix failure, measured on the QA rig: without the
        # supplementary group journalctl exits 0 with EMPTY stderr
        # under --output=json and hands back the service account's own
        # user journal, every system record missing. Nothing in the
        # subprocess result distinguishes that from a quiet host, so
        # the readability of the files is what we key on.
        (['/var/log/journal/m/system.journal'], False, False),
        (['/var/log/journal/m/system.journal'], True, True),
        # A user journal is readable by its own user, so it must not
        # count as evidence that the SYSTEM journal is visible.
        (['/var/log/journal/m/user-1000.journal'], True, True),
        # No system journal files at all: we cannot tell, so run the
        # read rather than refusing it.
        ([], False, True),
    ],
)
def test_a_hidden_system_journal_is_reported(
    fake_rdb: mock.MagicMock,
    files: list[str],
    readable: bool,
    expected_ok: bool,
) -> None:
    completed = mock.Mock(returncode=0, stdout='', stderr='')
    from anthias_host_agent.__main__ import execute_host_command

    with (
        mock.patch(f'{HOST_AGENT}.redis.Redis', return_value=fake_rdb),
        mock.patch(f'{HOST_AGENT}._run_journalctl', return_value=completed),
        mock.patch(f'{HOST_AGENT}.glob.glob', return_value=files),
        mock.patch(f'{HOST_AGENT}.os.access', return_value=readable),
        mock.patch(f'{HOST_AGENT}.os.path.getmtime', return_value=1.0),
    ):
        execute_host_command(journal.build_request('req-perm'))

    reply = json.loads(fake_rdb.rpush.call_args.args[1])
    assert reply['ok'] is expected_ok
    if not expected_ok:
        assert 'SupplementaryGroups=systemd-journal' in reply['error']


def test_an_unreadable_system_journal_never_runs_journalctl(
    fake_rdb: mock.MagicMock,
) -> None:
    from anthias_host_agent.__main__ import execute_host_command

    with (
        mock.patch(f'{HOST_AGENT}.redis.Redis', return_value=fake_rdb),
        mock.patch(f'{HOST_AGENT}._run_journalctl') as run,
        mock.patch(
            f'{HOST_AGENT}.glob.glob',
            return_value=['/var/log/journal/m/system.journal'],
        ),
        mock.patch(f'{HOST_AGENT}.os.access', return_value=False),
    ):
        execute_host_command(journal.build_request('req-perm2'))

    run.assert_not_called()


@pytest.mark.parametrize(
    ('var_files', 'run_files', 'mtimes', 'expected'),
    [
        # Raspberry Pi OS, measured on the rig: /var/log/journal
        # exists but is EMPTY, and a vendor drop-in pins
        # Storage=volatile. A directory-existence probe says
        # "persistent" on a board that discards its logs each reboot.
        ([], ['/run/log/journal/m/system.journal'], {}, False),
        # Previously persistent, since switched to volatile: the old
        # files are still in /var forever. An "any files present"
        # probe says "persistent" here too.
        (
            ['/var/log/journal/m/old.journal'],
            ['/run/log/journal/m/system.journal'],
            {
                '/var/log/journal/m/old.journal': 100.0,
                '/run/log/journal/m/system.journal': 200.0,
            },
            False,
        ),
        # Genuinely persistent: /var holds the live file, /run is empty.
        (
            ['/var/log/journal/m/system.journal'],
            [],
            {'/var/log/journal/m/system.journal': 200.0},
            True,
        ),
        # Just flushed: both exist, /var is the fresher one.
        (
            ['/var/log/journal/m/system.journal'],
            ['/run/log/journal/m/stale.journal'],
            {
                '/var/log/journal/m/system.journal': 300.0,
                '/run/log/journal/m/stale.journal': 100.0,
            },
            True,
        ),
    ],
)
def test_persistence_is_measured_from_which_location_is_live(
    fake_rdb: mock.MagicMock,
    var_files: list[str],
    run_files: list[str],
    mtimes: dict[str, float],
    expected: bool,
) -> None:
    completed = mock.Mock(returncode=0, stdout='', stderr='')
    from anthias_host_agent.__main__ import execute_host_command

    with (
        mock.patch(f'{HOST_AGENT}.redis.Redis', return_value=fake_rdb),
        mock.patch(f'{HOST_AGENT}._run_journalctl', return_value=completed),
        mock.patch(
            f'{HOST_AGENT}.glob.glob',
            side_effect=lambda pattern: (
                var_files
                if pattern == journal.PERSISTENT_JOURNAL_GLOB
                else run_files
            ),
        ),
        mock.patch(f'{HOST_AGENT}.os.path.getmtime', side_effect=mtimes.get),
        mock.patch(f'{HOST_AGENT}.os.access', return_value=True),
    ):
        execute_host_command(journal.build_request('req-persist'))

    reply = json.loads(fake_rdb.rpush.call_args.args[1])
    assert reply['persistent'] is expected


def test_host_agent_reports_a_volatile_journal(
    fake_rdb: mock.MagicMock,
) -> None:
    # No /var/log/journal means Storage=auto is volatile: the entries
    # are current-boot-only and the API has to be able to say so.
    completed = mock.Mock(returncode=0, stdout='', stderr='')
    replies = _run_journal_command(
        fake_rdb,
        request=journal.build_request('req-8'),
        run_result=completed,
        persistent=False,
    )
    assert replies[0]['persistent'] is False


def test_host_agent_ignores_a_journal_request_with_no_id(
    fake_rdb: mock.MagicMock,
) -> None:
    from anthias_host_agent.__main__ import execute_host_command

    with (
        mock.patch(f'{HOST_AGENT}.redis.Redis', return_value=fake_rdb),
        mock.patch(f'{HOST_AGENT}._run_journalctl') as run,
    ):
        execute_host_command(
            json.dumps({'cmd': journal.JOURNAL_LOGS_COMMAND, 'args': {}})
        )

    run.assert_not_called()


def test_a_redis_failure_delivering_a_reply_does_not_raise(
    fake_rdb: mock.MagicMock,
) -> None:
    # This runs inside the pubsub handler; raising would end
    # subscriber_loop and take reboot/shutdown handling down with it.
    fake_rdb.rpush.side_effect = redis.RedisError('broker gone')
    completed = mock.Mock(returncode=0, stdout='', stderr='')
    _run_journal_command(
        fake_rdb,
        request=journal.build_request('req-9'),
        run_result=completed,
    )


# ---------------------------------------------------------------------
# Server-side backends
# ---------------------------------------------------------------------


@pytest.fixture
def on_bare_metal() -> Any:
    with mock.patch(f'{SYSTEM_LOGS}.is_balena_app', return_value=False):
        yield


@pytest.fixture
def on_balena() -> Any:
    with mock.patch(f'{SYSTEM_LOGS}.is_balena_app', return_value=True):
        yield


def test_bare_metal_round_trip(on_bare_metal: Any) -> None:
    fake_redis = mock.MagicMock()
    fake_redis.publish.return_value = 1
    reply = json.dumps(
        {
            'ok': True,
            'payload': _payload(_record()),
            'truncated': False,
            'persistent': True,
        }
    )
    fake_redis.blpop.return_value = ('host:journal:x', reply)

    with mock.patch(
        f'{SYSTEM_LOGS}.connect_to_redis', return_value=fake_redis
    ):
        snapshot = system_logs.get_journal_snapshot(count=10, unit='docker')

    assert snapshot.source == system_logs.SOURCE_HOST_AGENT
    assert snapshot.persistent is True
    assert snapshot.entries[0]['message'] == 'hello'

    channel, published = fake_redis.publish.call_args.args
    assert channel == 'hostcmd'
    request = json.loads(published)
    assert request['cmd'] == journal.JOURNAL_LOGS_COMMAND
    assert request['args'] == {'count': 10, 'unit': 'docker'}
    # A correlation ID per request is what keeps two concurrent
    # requests from reading each other's results.
    assert request['id']
    assert fake_redis.blpop.call_args.args[0] == [
        journal.result_key(request['id'])
    ]


def test_bare_metal_blocking_pop_is_bounded(on_bare_metal: Any) -> None:
    # A dead or too-old host agent must produce a clean error, not a
    # hung request worker.
    fake_redis = mock.MagicMock()
    fake_redis.publish.return_value = 1
    fake_redis.blpop.return_value = None

    with (
        mock.patch(f'{SYSTEM_LOGS}.connect_to_redis', return_value=fake_redis),
        pytest.raises(system_logs.JournalUnavailable, match='Timed out'),
    ):
        system_logs.get_journal_snapshot()

    assert fake_redis.blpop.call_args.kwargs['timeout'] == (
        journal.REPLY_TIMEOUT_SECONDS
    )


def test_a_stopped_host_agent_fails_immediately(
    on_bare_metal: Any,
) -> None:
    # PUBLISH reports its subscriber count. Zero means no reply is
    # ever coming, so blocking for the full timeout would just pin a
    # worker thread -- and a log view that retries would stall the UI
    # on a device whose agent is simply down.
    fake_redis = mock.MagicMock()
    fake_redis.publish.return_value = 0

    with (
        mock.patch(f'{SYSTEM_LOGS}.connect_to_redis', return_value=fake_redis),
        pytest.raises(system_logs.JournalUnavailable, match='not running'),
    ):
        system_logs.get_journal_snapshot()

    fake_redis.blpop.assert_not_called()


def test_bare_metal_surfaces_the_agent_error(on_bare_metal: Any) -> None:
    fake_redis = mock.MagicMock()
    fake_redis.publish.return_value = 1
    fake_redis.blpop.return_value = (
        'k',
        json.dumps({'ok': False, 'error': 'No journal files were found.'}),
    )

    with (
        mock.patch(f'{SYSTEM_LOGS}.connect_to_redis', return_value=fake_redis),
        pytest.raises(
            system_logs.JournalUnavailable, match='No journal files'
        ),
    ):
        system_logs.get_journal_snapshot()


def test_bare_metal_handles_a_redis_outage(on_bare_metal: Any) -> None:
    fake_redis = mock.MagicMock()
    fake_redis.publish.side_effect = redis.RedisError('down')

    with (
        mock.patch(f'{SYSTEM_LOGS}.connect_to_redis', return_value=fake_redis),
        pytest.raises(system_logs.JournalUnavailable) as raised,
    ):
        system_logs.get_journal_snapshot()
    assert 'down' not in raised.value.user_message


def test_bare_metal_handles_an_unreadable_reply(on_bare_metal: Any) -> None:
    fake_redis = mock.MagicMock()
    fake_redis.publish.return_value = 1
    fake_redis.blpop.return_value = ('k', 'not json')

    with (
        mock.patch(f'{SYSTEM_LOGS}.connect_to_redis', return_value=fake_redis),
        pytest.raises(system_logs.JournalUnavailable, match='unreadable'),
    ):
        system_logs.get_journal_snapshot()


def _supervisor_response(
    *, ok: bool = True, status_code: int = 200, body: str = ''
) -> mock.Mock:
    response = mock.Mock()
    response.ok = ok
    response.status_code = status_code
    response.iter_content.return_value = iter([body.encode()])
    return response


def _container_id_response(
    container_id: str | None = 'a' * 64,
    *,
    ok: bool = True,
    status_code: int = 200,
) -> mock.Mock:
    response = mock.Mock()
    response.ok = ok
    response.status_code = status_code
    response.json.return_value = {
        'status': 'success',
        'containerId': container_id,
    }
    return response


def test_balena_round_trip(on_balena: Any) -> None:
    response = _supervisor_response(body=_payload(_record()))

    with mock.patch(
        f'{SYSTEM_LOGS}.get_balena_supervisor_api_response',
        return_value=response,
    ) as supervisor:
        snapshot = system_logs.get_journal_snapshot(
            count=50, unit='docker.service', since='-1h'
        )

    assert snapshot.source == system_logs.SOURCE_BALENA
    assert snapshot.entries[0]['message'] == 'hello'
    # balenaOS persists the journal.
    assert snapshot.persistent is True

    kwargs = supervisor.call_args.kwargs
    assert kwargs['action'] == 'journal-logs'
    assert kwargs['version'] == 'v2'
    assert kwargs['method'] == 'post'
    assert kwargs['timeout'] == (5, 30)
    # format: json is what makes the payload identical in shape to the
    # bare-metal journalctl output, so one normaliser serves both.
    assert kwargs['json'] == {
        'all': True,
        'follow': False,
        'format': 'json',
        'count': 50,
        'unit': 'docker.service',
        'since': '-1h',
    }
    response.close.assert_called_once()


def test_balena_resolves_a_container_name_to_an_id(on_balena: Any) -> None:
    # `container` means a NAME to the caller on both platforms, but
    # the supervisor only matches CONTAINER_ID_FULL. Forwarding the
    # name verbatim matches nothing and returns an empty 200 — the
    # same request answering on bare metal and silently blank here.
    calls = [
        _container_id_response('f' * 64),
        _supervisor_response(body=_payload(_record())),
    ]

    with mock.patch(
        f'{SYSTEM_LOGS}.get_balena_supervisor_api_response',
        side_effect=calls,
    ) as supervisor:
        system_logs.get_journal_snapshot(count=10, container='anthias-viewer')

    lookup, logs = supervisor.call_args_list
    assert lookup.kwargs['action'] == 'containerId'
    assert lookup.kwargs['params'] == {'serviceName': 'anthias-viewer'}
    assert logs.kwargs['json']['containerId'] == 'f' * 64


@pytest.mark.parametrize('container', ['a' * 64, 'b' * 12])
def test_balena_passes_a_container_id_straight_through(
    on_balena: Any, container: str
) -> None:
    # A caller already holding an ID shouldn't be put through a
    # pointless lookup round trip.
    with mock.patch(
        f'{SYSTEM_LOGS}.get_balena_supervisor_api_response',
        return_value=_supervisor_response(body=''),
    ) as supervisor:
        system_logs.get_journal_snapshot(count=10, container=container)

    assert supervisor.call_count == 1
    assert supervisor.call_args.kwargs['json']['containerId'] == container


@pytest.mark.parametrize(
    'lookup',
    [
        _container_id_response(ok=False, status_code=404),
        _container_id_response(None),
    ],
)
def test_balena_unknown_service_fails_loudly(
    on_balena: Any, lookup: mock.Mock
) -> None:
    # Not an empty 200. An unmatched filter that looks like a quiet
    # device is the exact failure this feature exists to stamp out.
    with (
        mock.patch(
            f'{SYSTEM_LOGS}.get_balena_supervisor_api_response',
            return_value=lookup,
        ),
        pytest.raises(system_logs.JournalUnavailable),
    ):
        system_logs.get_journal_snapshot(container='no-such-service')


def test_balena_never_asks_for_follow(on_balena: Any) -> None:
    # Snapshot-only on both platforms, deliberately: offering a live
    # tail on Balena alone would mean two divergent contracts.
    response = _supervisor_response(body='')
    with mock.patch(
        f'{SYSTEM_LOGS}.get_balena_supervisor_api_response',
        return_value=response,
    ) as supervisor:
        system_logs.get_journal_snapshot()
    assert supervisor.call_args.kwargs['json']['follow'] is False


def test_balena_caps_the_streamed_payload(on_balena: Any) -> None:
    response = mock.Mock()
    response.ok = True
    chunk = b'x' * (64 * 1024)
    # More chunks than the cap allows, so the reader must stop early
    # rather than buffering everything the supervisor offers.
    response.iter_content.return_value = iter([chunk] * 200)

    with mock.patch(
        f'{SYSTEM_LOGS}.get_balena_supervisor_api_response',
        return_value=response,
    ):
        snapshot = system_logs.get_journal_snapshot()

    assert snapshot.truncated is True


def test_balena_keeps_the_NEWEST_entries_when_capping(
    on_balena: Any,
) -> None:
    # journalctl emits oldest-first, so an over-cap payload has to be
    # trimmed from the FRONT. Keeping the first 4 MB would throw away
    # exactly the records an operator opened the log to read, and
    # would disagree with the host-agent backend.
    oldest = _payload(_record(MESSAGE='x' * 2_000_000))
    newest = _payload(_record(MESSAGE='the-newest-entry'))
    response = mock.Mock()
    response.ok = True
    response.iter_content.return_value = iter(
        [oldest.encode(), oldest.encode(), oldest.encode(), newest.encode()]
    )

    with mock.patch(
        f'{SYSTEM_LOGS}.get_balena_supervisor_api_response',
        return_value=response,
    ):
        snapshot = system_logs.get_journal_snapshot()

    assert snapshot.truncated is True
    assert snapshot.entries[-1]['message'] == 'the-newest-entry'


def test_balena_bounds_total_bytes_read(on_balena: Any) -> None:
    # `count` caps lines, not bytes. Without a stream ceiling a
    # supervisor emitting pathological messages runs until the socket
    # timeout.
    chunk = b'x' * (1024 * 1024)
    response = mock.Mock()
    response.ok = True
    response.iter_content.return_value = iter([chunk] * 10_000)

    with mock.patch(
        f'{SYSTEM_LOGS}.get_balena_supervisor_api_response',
        return_value=response,
    ):
        snapshot = system_logs.get_journal_snapshot()

    assert snapshot.truncated is True


def test_balena_reports_a_supervisor_error(on_balena: Any) -> None:
    response = _supervisor_response(ok=False, status_code=503)
    with (
        mock.patch(
            f'{SYSTEM_LOGS}.get_balena_supervisor_api_response',
            return_value=response,
        ),
        pytest.raises(system_logs.JournalUnavailable, match='503'),
    ):
        system_logs.get_journal_snapshot()
    response.close.assert_called_once()


@pytest.mark.parametrize(
    'failure',
    [
        requests.ConnectionError('private transport detail'),
        requests.ReadTimeout('private transport detail'),
        requests.exceptions.ChunkedEncodingError('private transport detail'),
    ],
)
def test_balena_reports_a_body_read_failure(
    on_balena: Any, failure: requests.RequestException
) -> None:
    response = _supervisor_response()

    def chunks() -> Any:
        yield _payload(_record()).encode()
        raise failure

    response.iter_content.return_value = chunks()
    with (
        mock.patch(
            f'{SYSTEM_LOGS}.get_balena_supervisor_api_response',
            return_value=response,
        ),
        pytest.raises(system_logs.JournalUnavailable) as raised,
    ):
        system_logs.get_journal_snapshot()

    assert 'private transport detail' not in raised.value.user_message
    assert raised.value.__cause__ is failure
    response.close.assert_called_once()


def test_balena_reports_an_unreachable_supervisor(on_balena: Any) -> None:
    with (
        mock.patch(
            f'{SYSTEM_LOGS}.get_balena_supervisor_api_response',
            side_effect=requests.ConnectionError('refused'),
        ),
        pytest.raises(
            system_logs.JournalUnavailable, match='Could not reach'
        ) as raised,
    ):
        system_logs.get_journal_snapshot()
    # Transport detail goes to the log, never into the API response.
    assert 'refused' not in raised.value.user_message


def test_filters_are_validated_before_either_backend_runs(
    on_bare_metal: Any,
) -> None:
    with (
        mock.patch(f'{SYSTEM_LOGS}.connect_to_redis') as connect,
        pytest.raises(journal.JournalRequestError),
    ):
        system_logs.get_journal_snapshot(unit='--follow')
    connect.assert_not_called()
