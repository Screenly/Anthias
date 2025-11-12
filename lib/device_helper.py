from __future__ import unicode_literals


def parse_cpu_info():
    """
    Extracts the various Raspberry Pi related data
    from the CPU.
    """
    cpu_info = {'cpu_count': 0}

    with open('/proc/cpuinfo', 'r') as cpuinfo:
        for line in cpuinfo:
            try:
                key = line.split(':')[0].strip()
                value = line.split(':')[1].strip()
            except Exception:
                pass

            if key == 'processor':
                cpu_info['cpu_count'] += 1

            if key in ['Serial', 'Hardware', 'Revision', 'Model']:
                cpu_info[key.lower()] = value
    return cpu_info


def get_device_type():
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
