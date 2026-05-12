def parse_cpu_info() -> dict[str, int | str]:
    """
    Extracts the various Raspberry Pi related data
    from the CPU.
    """
    cpu_info: dict[str, int | str] = {'cpu_count': 0}

    with open('/proc/cpuinfo', 'r') as cpuinfo:
        for line in cpuinfo:
            try:
                key = line.split(':')[0].strip()
                value = line.split(':')[1].strip()
            except Exception:
                pass

            if key == 'processor':
                cpu_info['cpu_count'] = (
                    int(cpu_info.get('cpu_count', 0) or 0) + 1
                )

            if key in ['Serial', 'Hardware', 'Revision', 'Model']:
                cpu_info[key.lower()] = value
    return cpu_info


def _read_sysfs(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
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
                return ' '.join(cleaned.split())
    except OSError:
        pass
    return ''


def get_friendly_device_model() -> str:
    """Operator-facing label for the host the player is running on.

    Pi:  whatever the firmware Model line reads
         ('Raspberry Pi 5 Model B Rev 1.0').
    x86: '<vendor> <product> · <CPU>' when DMI is exposed via
         /sys/class/dmi/id, otherwise just the CPU brand. Falls back
         to 'Generic x86_64 Device' when neither is readable so the
         System Info card never renders blank.
    """
    cpu_info = parse_cpu_info()
    pi_model = cpu_info.get('model')
    if isinstance(pi_model, str) and pi_model:
        return pi_model

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
    chassis = ' '.join(part for part in (vendor, product) if part).strip()

    parts = [p for p in (chassis, cpu_brand) if p]
    if parts:
        return ' · '.join(parts)

    from platform import machine

    return f'Generic {machine() or "x86_64"} Device'


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
