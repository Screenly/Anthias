"""Mirror the picture to every connected display on Wayland boards.

Only the cage boards (Pi 5, x86, generic arm64) can do this. Cage owns
every connector there, so mirroring is a layout question: place every
output at the same origin of the compositor's layout and they all
show the same region. Cage sizes its fullscreen view to the layout's
bounding box, so the viewer gets one canvas and every display shows
all of it.

That only lines up when every output has the same logical size, so
before overlapping them:

* if every display offers a common resolution, switch all of them to
  the largest one (pixel-exact, no scaling);
* otherwise keep each display's current mode and scale the others to
  the logical width of the smallest one, so the content still fills
  each screen. Displays with different aspect ratios then crop or
  letterbox the edge, which is the best a mirror can do.

Cage itself must be launched with ``-m extend``, or it disables every
output but one; ``bin/lib/viewer/platform_wayland.sh`` does that when
the ``display_mirroring`` setting is on and tells the viewer through
``ANTHIAS_CAGE_MIRRORING``.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Set by platform_wayland.sh to the mode cage was launched in.
CAGE_MIRRORING_ENV = 'ANTHIAS_CAGE_MIRRORING'

WLR_RANDR_TIMEOUT_S = 5


@dataclass(frozen=True)
class Mode:
    width: int
    height: int
    refresh: float
    preferred: bool = False

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def wlr_arg(self) -> str:
        # wlr-randr matches the refresh exactly in mHz after rounding
        # ``Hz * 1000``; it prints it with %f, so %f round-trips. A
        # zero refresh (headless/virtual outputs) means "any".
        if self.refresh > 0:
            return f'{self.width}x{self.height}@{self.refresh:f}Hz'
        return f'{self.width}x{self.height}'


@dataclass
class Output:
    name: str
    current: Mode
    modes: list[Mode] = field(default_factory=list)
    x: int = 0
    y: int = 0
    scale: float = 1.0


def parse_outputs(raw: str) -> list[Output]:
    """Enabled outputs with a current mode from ``wlr-randr --json``."""
    try:
        heads: Any = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(heads, list):
        return []
    outputs: list[Output] = []
    for head in heads:
        if not isinstance(head, dict) or not head.get('enabled'):
            continue
        modes: list[Mode] = []
        current: Mode | None = None
        for entry in head.get('modes') or []:
            try:
                mode = Mode(
                    width=int(entry['width']),
                    height=int(entry['height']),
                    refresh=float(entry.get('refresh') or 0),
                    preferred=bool(entry.get('preferred')),
                )
            except (KeyError, TypeError, ValueError):
                continue
            modes.append(mode)
            if entry.get('current'):
                current = mode
        if current is None:
            continue
        position = head.get('position') or {}
        outputs.append(
            Output(
                name=str(head.get('name')),
                current=current,
                modes=modes,
                x=int(position.get('x', 0)),
                y=int(position.get('y', 0)),
                scale=float(head.get('scale') or 1.0),
            )
        )
    return outputs


def _best_mode_for(output: Output, size: tuple[int, int]) -> Mode:
    candidates = [m for m in output.modes if m.size == size]
    if output.current.size == size:
        return output.current
    preferred = [m for m in candidates if m.preferred]
    if preferred:
        return preferred[0]
    return max(candidates, key=lambda m: m.refresh)


def _common_size(outputs: list[Output]) -> tuple[int, int] | None:
    sizes: set[tuple[int, int]] | None = None
    for output in outputs:
        offered = {m.size for m in output.modes} | {output.current.size}
        sizes = offered if sizes is None else sizes & offered
    if not sizes:
        return None
    return max(sizes, key=lambda s: (s[0] * s[1], s[0]))


def plan_mirror(outputs: list[Output]) -> list[str]:
    """``wlr-randr`` arguments that mirror ``outputs``.

    Returns an empty list when there is nothing to change: a single
    output, or a layout that is already mirrored.
    """
    if len(outputs) < 2:
        return []

    targets: dict[str, tuple[Mode | None, float]] = {}
    size = _common_size(outputs)
    if size is not None:
        for output in outputs:
            best = _best_mode_for(output, size)
            targets[output.name] = (
                None if best == output.current else best,
                1.0,
            )
    else:
        reference = min(o.current.width for o in outputs)
        for output in outputs:
            targets[output.name] = (None, output.current.width / reference)

    args: list[str] = []
    for output in outputs:
        mode, scale = targets[output.name]
        if (
            mode is None
            and output.x == 0
            and output.y == 0
            and abs(output.scale - scale) < 1e-3
        ):
            continue
        args += ['--output', output.name]
        if mode is not None:
            args += ['--mode', mode.wlr_arg()]
        args += ['--pos', '0,0', '--scale', f'{scale:g}']
    return args


def _wlr_randr(*args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ['wlr-randr', *args],
            capture_output=True,
            text=True,
            timeout=WLR_RANDR_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        logger.warning('wlr-randr unavailable for mirroring: %s', exc)
        return None


def apply_mirror() -> bool:
    """Overlay every enabled output so they all show the same picture.

    True once the layout is mirrored (or there is a single output, so
    nothing to mirror); False when cage isn't answering yet or rejected
    the configuration, so the caller retries later.
    """
    listing = _wlr_randr('--json')
    if listing is None or listing.returncode != 0:
        return False
    outputs = parse_outputs(listing.stdout)
    if not outputs:
        return False
    args = plan_mirror(outputs)
    if not args:
        return True
    result = _wlr_randr(*args)
    if result is None:
        return False
    if result.returncode != 0:
        logger.warning(
            'wlr-randr could not mirror outputs (exit %d): %s',
            result.returncode,
            (result.stderr or '').strip(),
        )
        return False
    logger.info(
        'Mirrored %d outputs: %s',
        len(outputs),
        ', '.join(o.name for o in outputs),
    )
    return True
