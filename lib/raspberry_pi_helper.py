def parse_cpu_info():
    """
    Extracts the various Raspberry Pi related data
    from the CPU.
    """
    cpu_info = {
        'cpu_count': 0
    }

    with open('/proc/cpuinfo', 'r') as cpuinfo:
        for line in cpuinfo:
            try:
                key = line.split(':')[0].strip()
                value = line.split(':')[1].strip()
            except Exception:
                pass

            if key == 'processor':
                cpu_info['cpu_count'] += 1

            if key in ['Serial', 'Hardware', 'Revision', 'model name']:
                cpu_info[key.lower()] = value
    return cpu_info


def lookup_raspberry_pi_revision(revision):
    """
    Takes the revision number and returns the
    manufacturer, ram and human readable hardware revision.

    Dataset is available here:
    https://www.raspberrypi.org/documentation/hardware/raspberrypi/revision-codes/README.md
    """

    database = {
        '900021': {'manufacturer': 'Sony UK',
                   'model': 'Model A+',
                   'ram': '512MB',
                   'revision': '1.1'},
        '900032': {'manufacturer': 'Sony UK',
                   'model': 'Model B+',
                   'ram': '512MB',
                   'revision': '1.2'},
        '900061': {'manufacturer': 'Sony UK',
                   'model': 'Model CM',
                   'ram': '512MB',
                   'revision': '1.1'},
        '900092': {'manufacturer': 'Sony UK',
                   'model': 'Model Zero',
                   'ram': '512MB',
                   'revision': '1.2'},
        '900093': {'manufacturer': 'Sony UK',
                   'model': 'Model Zero',
                   'ram': '512MB',
                   'revision': '1.3'},
        '9000c1': {'manufacturer': 'Sony UK',
                   'model': 'Model Zero W',
                   'ram': '512MB',
                   'revision': '1.1'},
        '9020e0': {'manufacturer': 'Sony UK',
                   'model': 'Model 3A+',
                   'ram': '512MB',
                   'revision': '1.0'},
        '920092': {'manufacturer': 'Embest',
                   'model': 'Model Zero',
                   'ram': '512MB',
                   'revision': '1.2'},
        '920093': {'manufacturer': 'Embest',
                   'model': 'Model Zero',
                   'ram': '512MB',
                   'revision': '1.3'},
        'a01040': {'manufacturer': 'Sony UK',
                   'model': 'Model 2B',
                   'ram': '1GB',
                   'revision': '1.0'},
        'a01041': {'manufacturer': 'Sony UK',
                   'model': 'Model 2B',
                   'ram': '1GB',
                   'revision': '1.1'},
        'a02042': {'manufacturer': 'Sony UK',
                   'model': 'Model 2B (with BCM2837)',
                   'ram': '1GB',
                   'revision': '1.2'},
        'a02082': {'manufacturer': 'Sony UK',
                   'model': 'Model 3B',
                   'ram': '1GB',
                   'revision': '1.2'},
        'a020a0': {'manufacturer': 'Sony UK',
                   'model': 'Model CM3',
                   'ram': '1GB',
                   'revision': '1.0'},
        'a020d3': {'manufacturer': 'Sony UK',
                   'model': 'Model 3B+',
                   'ram': '1GB',
                   'revision': '1.3'},
        'a02100': {'manufacturer': 'Sony UK',
                   'model': 'Model CM3+',
                   'ram': '1GB',
                   'revision': '1.0'},
        'a03111': {'manufacturer': 'Sony UK',
                   'model': 'Model 4B',
                   'ram': '1GB',
                   'revision': '1.1'},
        'a03115': {'manufacturer': 'Sony UK',
                   'model': 'Model 4B',
                   'ram': '1GB',
                   'revision': '1.5'},
        'a21041': {'manufacturer': 'Embest',
                   'model': 'Model 2B',
                   'ram': '1GB',
                   'revision': '1.1'},
        'a22042': {'manufacturer': 'Embest',
                   'model': 'Model 2B (with BCM2837)',
                   'ram': '1GB',
                   'revision': '1.2'},
        'a22082': {'manufacturer': 'Embest',
                   'model': 'Model 3B',
                   'ram': '1GB',
                   'revision': '1.2'},
        'a22083': {'manufacturer': 'Embest',
                   'model': 'Model 3B',
                   'ram': '1GB',
                   'revision': '1.3'},
        'a220a0': {'manufacturer': 'Embest',
                   'model': 'Model CM3',
                   'ram': '1GB',
                   'revision': '1.0'},
        'a32082': {'manufacturer': 'Sony Japan',
                   'model': 'Model 3B',
                   'ram': '1GB',
                   'revision': '1.2'},
        'a52082': {'manufacturer': 'Stadium',
                   'model': 'Model 3B',
                   'ram': '1GB',
                   'revision': '1.2'},
        'b03111': {'manufacturer': 'Sony UK',
                   'model': 'Model 4B',
                   'ram': '2GB',
                   'revision': '1.1'},
        'b03112': {'manufacturer': 'Sony UK',
                   'model': 'Model 4B',
                   'ram': '2GB',
                   'revision': '1.2'},
        'b03114': {'manufacturer': 'Sony UK',
                   'model': 'Model 4B',
                   'ram': '2GB',
                   'revision': '1.4'},
        'b03115': {'manufacturer': 'Sony UK',
                   'model': 'Model 4B',
                   'ram': '2GB',
                   'revision': '1.5'},
        'c03111': {'manufacturer': 'Sony UK',
                   'model': 'Model 4B',
                   'ram': '4GB',
                   'revision': '1.1'},
        'c03112': {'manufacturer': 'Sony UK',
                   'model': 'Model 4B',
                   'ram': '4GB',
                   'revision': '1.2'},
        'c03130': {'manufacturer': 'Sony UK',
                   'model': 'Model Pi 400',
                   'ram': '4GB',
                   'revision': '1'},
        'd03114': {'manufacturer': 'Sony UK',
                   'model': 'Model 4B',
                   'ram': '8GB',
                   'revision': '1.4'},
        'c03114': {'manufacturer': 'Sony UK',
                   'model': 'Model 4B',
                   'ram': '4GB',
                   'revision': '1.4'},
        'd03115': {'manufacturer': 'Sony UK',
                   'model': 'Model 4B',
                   'ram': '8GB',
                   'revision': '1.5'},
        'c03115': {'manufacturer': 'Sony UK',
                   'model': 'Model 4B',
                   'ram': '4GB',
                   'revision': '1.5'},
        'unknown': {'manufacturer': 'Unknown',
                    'model': 'Unknown',
                    'ram': '0GB',
                    'revision': '0.0'}
    }

    return database.get(revision) or database.get('unknown')
