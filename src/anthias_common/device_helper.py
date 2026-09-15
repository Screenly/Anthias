# Upper bound on a single firmware-supplied string. Real values are
# far shorter — 'Raspberry Pi 5 Model B Rev 1.0' is 30 characters —
# so this only ever truncates something malformed or hostile.
_MAX_FIRMWARE_STRING_LEN = 128

# Upper bound on a firmware-supplied *file*, applied at the read so a
# huge property never lands in memory in the first place. Device-tree
# properties and DMI fields are a few dozen bytes; 4 KiB is generous.
_MAX_FIRMWARE_READ_BYTES = 4096


def sanitize_firmware_string(value: str) -> str:
    """Normalise a string that came from firmware rather than from us.

    Device-tree properties, DMI/SMBIOS fields and ``/proc/cpuinfo``
    lines are all data we merely read: a board vendor's DTB, an OEM's
    SMBIOS tables, or — the case needing no physical access — the
    synthetic DMI a hypervisor hands a VM guest. What we read ends up
    in an HTML page, a JSON API response, a Redis value, a log line
    and an outbound telemetry field.

    This is deliberately *not* what stops injection at those sinks —
    Django autoescapes the template and DRF JSON-encodes the API, and
    that stays true regardless. It covers what no sink handles:

    * **length** — nothing else bounds these strings, and they fan out
      to every render of the System Info page and every ``/api/v2/info``
      response;
    * **control characters** — terminal escape sequences reaching an
      operator's ``journalctl``, or NULs mid-value;
    * **bidi / zero-width characters** — the class that makes a label
      render as something other than what it says.

    Kept narrow on purpose: fold whitespace, drop non-printables, cap
    the length. Every string a real board reports survives unchanged.
    """
    # Device-tree properties are NUL-terminated, and a property
    # holding a *list* packs several strings into one buffer — stop at
    # the first terminator instead of concatenating across it.
    head = value.partition('\x00')[0]
    # Fold whitespace before dropping non-printables, so a newline
    # separates two words instead of welding them together.
    folded = ''.join(' ' if ch.isspace() else ch for ch in head)
    # str.isprintable() is False for C0/C1 controls, surrogates and
    # the format category — which is where the bidi overrides and
    # zero-width characters live — while leaving ordinary space True.
    printable = ''.join(ch for ch in folded if ch.isprintable())
    return ' '.join(printable.split())[:_MAX_FIRMWARE_STRING_LEN]


def read_firmware_file(path: str) -> str:
    """Read a firmware-supplied file, sanitised, ``''`` if unreadable.

    The bounded read is the point: an oversized or malformed property
    never lands in memory whole just to be truncated afterwards. Every
    caller reading firmware-authored bytes (device tree, DMI, the
    Sentry board tag) goes through here so the bound and the
    normalisation can't drift apart between them.
    """
    try:
        with open(path, 'rb') as f:
            raw = f.read(_MAX_FIRMWARE_READ_BYTES)
    except OSError:
        return ''
    return sanitize_firmware_string(raw.decode('utf-8', 'replace'))


def parse_cpu_info() -> dict[str, int | str]:
    """
    Extracts the various Raspberry Pi related data
    from the CPU.
    """
    cpu_info: dict[str, int | str] = {'cpu_count': 0}

    with open('/proc/cpuinfo', 'r') as cpuinfo:
        for line in cpuinfo:
            parts = line.split(':', 1)
            if len(parts) != 2:
                continue
            key = parts[0].strip()
            value = parts[1].strip()

            if key == 'processor':
                cpu_info['cpu_count'] = (
                    int(cpu_info.get('cpu_count', 0) or 0) + 1
                )

            if key in ['Serial', 'Hardware', 'Revision', 'Model']:
                # ``Model`` reaches the System Info card, /api/v2/info
                # and the telemetry payload; on a Pi the firmware
                # sources it from the device tree, so it gets the same
                # treatment as every other firmware string.
                cpu_info[key.lower()] = sanitize_firmware_string(value)
    return cpu_info


def _read_sysfs(path: str) -> str:
    try:
        with open(path) as f:
            return sanitize_firmware_string(f.read(_MAX_FIRMWARE_READ_BYTES))
    except OSError:
        return ''


def _read_cpu_brand() -> str:
    """First 'model name' line from /proc/cpuinfo, normalised.

    Drops marketing crud ((R), (TM)), the trailing 'CPU' token that
    the kernel parrots from CPUID, and the 'with <X> Graphics' suffix
    AMD APUs tack on (operators care about the CPU, not the iGPU).
    Yields 'Intel Core i7-9700K @ 3.60GHz' / 'AMD Ryzen 7 5700G'.
    """
    try:
        with open('/proc/cpuinfo') as f:
            for line in f:
                if not line.startswith('model name'):
                    continue
                raw = line.split(':', 1)[1].strip()
                cleaned = (
                    raw.replace('(R)', '')
                    .replace('(TM)', '')
                    .replace(' CPU ', ' ')
                )
                # Strip the ' with X Graphics' suffix using simple
                # string ops — avoids the regex polynomial-backtracking
                # warning Sonar flags on nested-quantifier patterns.
                lower = cleaned.lower()
                with_idx = lower.find(' with ')
                if with_idx != -1 and lower.rstrip().endswith('graphics'):
                    cleaned = cleaned[:with_idx]
                return sanitize_firmware_string(cleaned)
    except OSError:
        pass
    return ''


# Trailing corporate-suffix tokens peeled off a DMI vendor string.
# Compared case-insensitively with any trailing comma stripped, so
# 'Co.,' matches 'co.'. Multi-token suffixes like 'Co., Ltd.' fall out
# of the peel loop naturally.
_VENDOR_SUFFIX_TOKENS = frozenset(
    {
        'corporation',
        'incorporated',
        'corp.',
        'corp',
        'inc.',
        'inc',
        'ltd.',
        'ltd',
        'co.',
        'co',
        'llc',
        'gmbh',
        'ag',
    }
)


def _strip_corporate_suffix(vendor: str) -> str:
    """Drop trailing corporate-suffix tokens from a DMI vendor string.

    'Intel Corporation' -> 'Intel', 'Dell Inc.' -> 'Dell',
    'ASUSTeK Computer INC.' -> 'ASUSTeK Computer', 'Foo Co., Ltd.' ->
    'Foo'. Leaves a vendor that is *only* a suffix untouched so we never
    return an empty string here (the caller decides the fallback).
    """
    tokens = vendor.split()
    while (
        len(tokens) > 1
        and tokens[-1].lower().rstrip(',') in _VENDOR_SUFFIX_TOKENS
    ):
        tokens.pop()
    return ' '.join(tokens)


def read_device_tree_model() -> str:
    """Host board name from the device tree, ``''`` when there is none.

    The kernel writes ``/proc/device-tree/model`` as a NUL-terminated
    UTF-8 string ('FriendlyElec NanoPi R3S LTS', 'Radxa ROCK Pi 4B').
    x86 hosts have no device tree at all.

    **Only readable where ``/sys/firmware`` is visible.**
    ``/proc/device-tree`` is a symlink to
    ``/sys/firmware/devicetree/base``, and ``/sys/firmware`` is on
    Docker's default masked-paths list — so an *unprivileged*
    container (anthias-server, anthias-celery) sees the symlink but
    an empty target and gets ``''`` here, on every board. The host
    and privileged containers (anthias-viewer) read it fine.

    That is why ``anthias_host_agent`` publishes the value to Redis
    and ``anthias_common.board`` reads it from there: the server
    cannot obtain it on its own. Bind-mounting the tree in is not an
    option — a mount onto the masked path is still empty, and x86
    hosts have no source path to mount.
    """
    return read_firmware_file('/proc/device-tree/model')


def get_device_model_parts(dt_model: str | None = None) -> tuple[str, str]:
    """(primary, secondary) label for the host, for a two-line card.

    Returns the board/chassis as the primary line and the CPU brand as
    the secondary line so the System Info card can stack them rather than
    cramming both onto one row joined by a separator.

    Pi:  ('Raspberry Pi 5 Model B Rev 1.0', '') — the firmware Model
         line, no separate CPU line.
    SBC: ('FriendlyElec NanoPi R3S LTS', '') — non-Pi boards write no
         cpuinfo Model line and expose no DMI, so the device tree is
         the only thing that names them.
    x86: ('Whiskey Platform', 'Intel Celeron 4205U @ 1.80GHz') when DMI
         exposes a real chassis; ('Intel Celeron ...', '') when it only
         yields a CPU. Falls back to ('Generic x86_64 Device', '') when
         neither is readable so the card never renders blank.

    ``dt_model`` lets a caller inside an unprivileged container supply
    the device-tree model it got from Redis, since it cannot read the
    tree itself (see ``read_device_tree_model``); ``None`` means "read
    it here", which is right on the host and in privileged containers.
    Use ``anthias_common.board.get_device_model_parts`` to get the
    Redis-resolved value wired in.
    """
    cpu_info = parse_cpu_info()
    pi_model = cpu_info.get('model')
    if isinstance(pi_model, str) and pi_model:
        return pi_model, ''

    if dt_model is None:
        dt_model = read_device_tree_model()
    if dt_model:
        return dt_model, ''

    vendor = _read_sysfs('/sys/class/dmi/id/sys_vendor')
    product = _read_sysfs('/sys/class/dmi/id/product_name')
    cpu_brand = _read_cpu_brand()

    # Skip placeholder DMI strings OEMs ship from the factory or that
    # hypervisors expose to the guest — rendering 'QEMU Standard PC'
    # or 'System manufacturer System Product Name' is uglier than
    # just falling back to the CPU brand.
    placeholders = {
        '',
        'To Be Filled By O.E.M.',
        'System manufacturer',
        'System Product Name',
        'Default string',
        'Not Specified',
        'None',
    }
    placeholder_substrings = (
        'QEMU',
        'VMware',
        'VirtualBox',
        'innotek',
        'Bochs',
        'Xen ',
        'KVM',
        'Microsoft Corporation Virtual',
        'Hyper-V',
        'OpenStack',
        'Standard PC',
    )

    def _looks_virtual(value: str) -> bool:
        return any(needle in value for needle in placeholder_substrings)

    if vendor in placeholders or _looks_virtual(vendor):
        vendor = ''
    if product in placeholders or _looks_virtual(product):
        product = ''

    # Trim the corporate suffix DMI vendors carry ('Intel Corporation',
    # 'Dell Inc.', 'ASUSTeK Computer INC.') — noise in a device label.
    vendor = _strip_corporate_suffix(vendor)

    # Drop the board vendor when the CPU brand already names it. Whitebox
    # / reference boards set sys_vendor to the CPU maker ('Intel
    # Corporation' next to an 'Intel Celeron ...' CPU), which would stutter
    # as 'Intel ...' on both the board and CPU lines. Branded OEM boxes
    # (Dell, Lenovo) keep their vendor because it differs from the CPU.
    if (
        vendor
        and cpu_brand
        and vendor.split()[0].lower() in cpu_brand.lower().split()
    ):
        vendor = ''

    chassis = ' '.join(part for part in (vendor, product) if part).strip()

    if chassis:
        # Board on the primary line, CPU (when known) on the secondary.
        return chassis, cpu_brand
    if cpu_brand:
        return cpu_brand, ''

    from platform import machine

    return f'Generic {machine() or "x86_64"} Device', ''


def get_device_type() -> str:
    try:
        with open('/proc/device-tree/model') as file:
            content = file.read()

            if 'Raspberry Pi 5' in content or 'Compute Module 5' in content:
                return 'pi5'
            elif 'Raspberry Pi 4' in content or 'Compute Module 4' in content:
                return 'pi4'
            elif 'Raspberry Pi 3' in content or 'Compute Module 3' in content:
                return 'pi3'
            elif 'Raspberry Pi 2' in content:
                return 'pi2'
            else:
                return 'pi1'
    except FileNotFoundError:
        return 'x86'


def read_device_tree_compatibles() -> tuple[str, ...]:
    """Root-node ``compatible`` entries, most specific first.

    The kernel writes ``/proc/device-tree/compatible`` as a
    NUL-separated list running board-then-SoC, e.g.
    ``('friendlyarm,nanopi-r3s-lts', 'rockchip,rk3566')``. Subject to
    the same masking as ``read_device_tree_model`` — empty tuple in an
    unprivileged container, and on any host without a device tree.

    Bounded and sanitised like every other firmware string (see
    ``sanitize_firmware_string``). These entries only ever get
    compared against a fixed lookup table, so a hostile value can't
    do more than fail to match — but the read still has to be
    bounded, and the count capped, so a property packed with entries
    can't be walked indefinitely.
    """
    try:
        with open('/proc/device-tree/compatible', 'rb') as f:
            raw = f.read(_MAX_FIRMWARE_READ_BYTES).decode('utf-8', 'replace')
    except OSError:
        return ()
    entries = (sanitize_firmware_string(entry) for entry in raw.split('\x00'))
    return tuple(entry for entry in entries if entry)[
        :_MAX_DEVICE_TREE_COMPATIBLES
    ]


# A root node's compatible list is board-then-SoC, usually two
# entries and rarely more than a handful. The cap bounds the walk
# without excluding anything a real device tree declares.
_MAX_DEVICE_TREE_COMPATIBLES = 16


# SoC ``compatible`` string → board subtype, for silicon whose decode
# envelope we've measured. Keyed on the SoC rather than a vendor's
# model string because the capability is a property of the chip: every
# RK3566 board carries the same 4x Cortex-A55 and the same VPU, so one
# entry covers NanoPi, Radxa, Orange Pi and the rest without a table
# row each.
_SOC_COMPATIBLE_SUBTYPES: dict[str, str] = {
    'rockchip,rk3566': 'rk3566',
}


def detect_board_subtype() -> str | None:
    """Identify a non-Pi SBC by reading ``/proc/device-tree/model``.

    Returns a stable short token (e.g. ``'rockpi4'``) when the model
    string matches a known board, or ``None`` for unknown boards /
    hosts without a device tree.

    Two levels, model first then SoC: the model string pins a
    specific board (``'rockpi4'``), and the root ``compatible`` list's
    SoC entry covers every board built on silicon we've profiled
    (``rockchip,rk3566`` → ``'rk3566'``). The SoC level is what keeps
    the table from needing a row per vendor model, since the decode
    envelope is a property of the chip.

    ``bin/install.sh`` writes ``DEVICE_TYPE=arm64`` for every aarch64
    SBC it doesn't recognise as a Pi. Without a subtype the asset
    processor's codec gate falls back to the conservative empty arm64
    set, which rejects every video upload; the subtype is what selects
    a real envelope.

    Two callers share this single source of truth:

    * ``anthias_host_agent`` (docker-compose installs) detects on the
      host and publishes the token to Redis at ``host:board_subtype``.
    * ``anthias_common.board.get_board_subtype`` falls back to calling
      this directly when Redis has no value. That fallback only works
      where the device tree is actually readable — the host and
      privileged containers. In an unprivileged one it returns
      ``None`` whatever the board, because Docker masks
      ``/sys/firmware`` (see ``read_device_tree_model``), so the
      host_agent-published value is what carries this on
      docker-compose installs.
    """
    model_low = read_device_tree_model().lower()
    # "Radxa ROCK Pi 4B" (and 4A / 4C variants — all RK3399).
    if 'rock pi 4' in model_low:
        return 'rockpi4'
    # Fall back to the SoC. A board we've never seen still gets the
    # right envelope when its silicon is one we've measured, and the
    # model string stays the override for boards that need to differ
    # from their SoC default.
    for compatible in read_device_tree_compatibles():
        subtype = _SOC_COMPATIBLE_SUBTYPES.get(compatible)
        if subtype:
            return subtype
    return None
