SHORT_HASH_LENGTH = 7
BUILD_TARGET_OPTIONS = [
    'pi2',
    'pi3',
    'pi3-64',
    'pi4-64',
    'pi5',
    'x86',
    'arm64',
]
SERVICES = (
    'server',
    'redis',
    'viewer',
    'caddy',
    'test',
)
GITHUB_REPO_URL = 'https://github.com/Screenly/Anthias'
BASE_IMAGE = 'mirror.gcr.io/library/debian'

# Upstream Caddy, rebuilt as ghcr.io/screenly/anthias-caddy so the
# release pipeline signs it alongside the other images. Pinned to an
# exact release; bump deliberately.
CADDY_BASE_IMAGE = 'mirror.gcr.io/library/caddy:2.11.9-alpine'
