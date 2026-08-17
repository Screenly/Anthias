"""
A stand-in for the `docker` CLI, for exercising bin/purge_stale_images.sh
without a Docker daemon.

The image/container inventory lives in the JSON file named by
FAKE_DOCKER_STATE and is written back after every mutating call, so a
test can run the script twice and assert the second run is a no-op.
Removals are also appended to FAKE_DOCKER_REMOVED_LOG, one reference per
line, which is what the assertions read.

Only the handful of invocations the script actually makes are
implemented; anything else exits non-zero so a new call site shows up as
a test failure rather than as silently faked output.
"""

import fnmatch
import json
import os
import sys
from typing import Any

State = dict[str, Any]


def load_state() -> State:
    with open(os.environ['FAKE_DOCKER_STATE']) as f:
        state: State = json.load(f)
        return state


def save_state(state: State) -> None:
    with open(os.environ['FAKE_DOCKER_STATE'], 'w') as f:
        json.dump(state, f)


def log_removal(reference: str) -> None:
    path = os.environ.get('FAKE_DOCKER_REMOVED_LOG')
    if not path:
        return
    with open(path, 'a') as f:
        f.write(f'{reference}\n')


def in_use_image_ids(state: State) -> set[str]:
    ids: set[str] = set(state['containers'].values())
    return ids


def handle_ps(args: list[str]) -> int:
    if args != ['-aq']:
        return 1

    state = load_state()
    for container_id in state['containers']:
        print(container_id)
    return 0


def handle_inspect(args: list[str]) -> int:
    if args[0] != '--format':
        return 1

    fmt = args[1]
    targets = args[2:]
    state = load_state()

    if fmt == '{{.Image}}':
        for container_id in targets:
            if container_id not in state['containers']:
                return 1
            print(state['containers'][container_id])
        return 0

    return 1


def handle_images(args: list[str]) -> int:
    quiet = False
    references: list[str] = []
    dangling = False
    fmt: str | None = None
    index = 0

    while index < len(args):
        arg = args[index]
        if arg == '-q':
            quiet = True
        elif arg == '--no-trunc':
            pass
        elif arg == '--filter':
            index += 1
            key, _, value = args[index].partition('=')
            if key == 'reference':
                references.append(value)
            elif key == 'dangling':
                dangling = value == 'true'
            else:
                return 1
        elif arg == '--format':
            index += 1
            fmt = args[index]
        else:
            return 1
        index += 1

    state = load_state()

    if dangling:
        if not quiet:
            return 1
        for image in state['images']:
            if not image.get('repo'):
                print(image['id'])
        return 0

    if fmt != '{{.Repository}}:{{.Tag}} {{.ID}}':
        return 1

    for image in state['images']:
        repo = image.get('repo')
        if not repo:
            continue
        if references and not any(
            fnmatch.fnmatchcase(repo, pattern) for pattern in references
        ):
            continue
        tag = image.get('tag') or '<none>'
        print(f'{repo}:{tag} {image["id"]}')
    return 0


def handle_rmi(args: list[str]) -> int:
    if len(args) != 1:
        return 1

    reference = args[0]
    state = load_state()
    in_use = in_use_image_ids(state)

    # Removing by ID is only allowed when the ID answers to a single
    # reference, which is how the daemon behaves.
    if any(image['id'] == reference for image in state['images']):
        rows = [i for i in state['images'] if i['id'] == reference]
        if len(rows) > 1:
            print(
                f'Error response from daemon: conflict: unable to delete '
                f'{reference[7:19]} (must be forced) - image is referenced '
                f'in multiple repositories',
                file=sys.stderr,
            )
            return 1

    for image in state['images']:
        tagged = f'{image.get("repo")}:{image.get("tag") or "<none>"}'
        if reference not in (image['id'], tagged):
            continue

        if image['id'] in in_use:
            print(
                f'Error response from daemon: conflict: unable to delete '
                f'{reference} (must be forced)',
                file=sys.stderr,
            )
            return 1

        state['images'].remove(image)
        save_state(state)
        log_removal(reference)
        return 0

    print(
        f'Error response from daemon: No such image: {reference}',
        file=sys.stderr,
    )
    return 1


def handle_image(args: list[str]) -> int:
    if args[:2] != ['prune', '-f']:
        return 1

    state = load_state()
    in_use = in_use_image_ids(state)
    reclaimed = 0

    for image in list(state['images']):
        if image.get('repo'):
            continue
        if image['id'] in in_use:
            continue
        state['images'].remove(image)
        reclaimed += image.get('size', 1024)
        log_removal(image['id'])

    save_state(state)
    print(f'Total reclaimed space: {reclaimed}B')
    return 0


def main(argv: list[str]) -> int:
    if os.environ.get('FAKE_DOCKER_FAIL') == '1':
        print('Cannot connect to the Docker daemon.', file=sys.stderr)
        return 1

    if not argv:
        return 1

    handlers = {
        'ps': handle_ps,
        'inspect': handle_inspect,
        'images': handle_images,
        'image': handle_image,
        'rmi': handle_rmi,
    }
    handler = handlers.get(argv[0])
    if handler is None:
        return 1
    return handler(argv[1:])


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
