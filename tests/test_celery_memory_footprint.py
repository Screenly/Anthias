"""Keep the Celery worker small enough for a 1 GB board.

On a 1 GB Pi 3 (64-bit) the worker's default pool, one prefork child
per core, plus beat and the parent took about 600 MB, each process
carrying the whole Django app. The kernel then OOM-killed the viewer,
and every respawn stalled paging its own code back in from the SD card,
so the screen never came up. Two things keep that from coming back:

* below ``LOW_RAM_THRESHOLD_KB`` the worker runs a smaller pool and
  recycles a child that grows too large;
* importing the worker (and setting up Django, which every uvicorn and
  celery process does) does not load the ``cryptography`` stack, about
  15 MB per process. It belongs to the multi-screen PKI and is
  imported where a certificate is actually handled.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from anthias_common.board import LOW_RAM_THRESHOLD_KB, read_mem_total_kb
from anthias_server.celery_tasks import (
    LOW_RAM_WORKER_CONCURRENCY,
    LOW_RAM_WORKER_MAX_MEMORY_PER_CHILD_KB,
    low_ram_worker_settings,
)

REPO = Path(__file__).resolve().parents[1]


def test_read_mem_total_kb_parses_meminfo(tmp_path: Path) -> None:
    meminfo = tmp_path / 'meminfo'
    meminfo.write_text(
        'MemTotal:         878032 kB\n'
        'MemFree:           20444 kB\n'
        'SwapTotal:        439012 kB\n'
    )
    assert read_mem_total_kb(str(meminfo)) == 878032


@pytest.mark.parametrize(
    'content',
    ['MemFree: 20444 kB\n', 'MemTotal: lots kB\n', 'MemTotal:\n', ''],
)
def test_read_mem_total_kb_unparseable_is_unknown(
    tmp_path: Path, content: str
) -> None:
    meminfo = tmp_path / 'meminfo'
    meminfo.write_text(content)
    assert read_mem_total_kb(str(meminfo)) is None


def test_read_mem_total_kb_missing_file_is_unknown(tmp_path: Path) -> None:
    assert read_mem_total_kb(str(tmp_path / 'absent')) is None


def test_low_ram_board_gets_a_small_pool() -> None:
    # A 1 GB Pi 3 with gpu_mem=128 reports about 878 MB.
    assert low_ram_worker_settings(878032) == {
        'worker_concurrency': LOW_RAM_WORKER_CONCURRENCY,
        'worker_max_memory_per_child': (
            LOW_RAM_WORKER_MAX_MEMORY_PER_CHILD_KB
        ),
    }
    assert low_ram_worker_settings(LOW_RAM_THRESHOLD_KB - 1) != {}


@pytest.mark.parametrize('total_kb', [LOW_RAM_THRESHOLD_KB, 3_884_000, None])
def test_other_boards_keep_celery_defaults(total_kb: int | None) -> None:
    assert low_ram_worker_settings(total_kb) == {}


def test_low_ram_pool_leaves_room_beside_a_publish() -> None:
    # controller.tasks.publish_slots leaves one process for the
    # display schedule and the rest; a pool of one would leave none.
    assert LOW_RAM_WORKER_CONCURRENCY >= 2


def test_worker_import_does_not_load_the_pki_stack() -> None:
    # A fresh interpreter: in this one other tests have long since
    # imported cryptography for their own reasons.
    script = (
        'import sys, django\n'
        'django.setup()\n'
        'import anthias_server.celery_tasks\n'
        'heavy = sorted(m for m in sys.modules if m.startswith(\n'
        "    ('cryptography.hazmat', 'cryptography.x509',\n"
        "     'cryptography.fernet')))\n"
        'print(heavy)\n'
    )
    env = dict(
        os.environ,
        DJANGO_SETTINGS_MODULE='anthias_server.django_project.settings',
        ENVIRONMENT='test',
    )
    result = subprocess.run(
        [sys.executable, '-c', script],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == '[]'
