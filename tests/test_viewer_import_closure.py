"""The viewer image must not need celery to start.

``anthias_viewer`` imports a handful of ``anthias_server`` modules for
their models, settings and the CEC request-reply client. The viewer
image installs only the ``viewer`` dependency group (see
``docker/uv-builder.j2``), which deliberately ships neither ``celery``
nor its ``billiard`` pool — so a module-level ``from celery...`` added
anywhere in that import closure crashes the viewer container at startup
with ``ModuleNotFoundError``, on every board, with no test failure to
warn you.

It is an easy mistake to make, because the offending line is added to a
server module that reads as server-only. This walks the closure and
pins it.

Imports inside a function or method are fine and are ignored: they only
run on a code path the viewer does not take.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'src'

#: Packages the viewer image does not install. ``billiard`` is celery's
#: fork of multiprocessing and is where ``SoftTimeLimitExceeded``
#: actually lives, so naming celery alone would not be enough.
FORBIDDEN_ROOTS = frozenset({'celery', 'billiard', 'kombu'})

#: First-party roots to follow when building the closure. Third-party
#: packages are recorded but not descended into.
FIRST_PARTY_ROOTS = ('anthias_viewer', 'anthias_server', 'anthias_common')


def _module_path(module: str) -> Path | None:
    """Resolve a dotted module name to a file under ``src/``."""
    base = SRC / Path(*module.split('.'))
    for candidate in (base.with_suffix('.py'), base / '__init__.py'):
        if candidate.is_file():
            return candidate
    return None


def _module_level_imports(
    tree: ast.Module,
) -> tuple[list[tuple[str, int]], set[str]]:
    """``(imported_modules, follow_candidates)`` outside any function.

    Module-level ``try``/``if`` blocks still execute on import, so they
    count; anything inside a def or a class body does not.

    ``from pkg import a, b`` names ``pkg`` for the forbidden-root check,
    but ``a``/``b`` may themselves be submodules to descend into — the
    form ``from anthias_server.lib import cec_client`` is how the viewer
    reaches half of its closure. Those go in the second set, where a
    name that turns out to be an ordinary attribute simply fails to
    resolve to a file and is dropped.
    """
    found: list[tuple[str, int]] = []
    candidates: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child,
                ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
            ):
                continue
            if isinstance(child, ast.Import):
                found.extend(
                    (alias.name, child.lineno) for alias in child.names
                )
            elif isinstance(child, ast.ImportFrom):
                # ``level`` > 0 is a relative import — first-party by
                # construction, and not resolvable to a root package
                # name here.
                if child.module and not child.level:
                    found.append((child.module, child.lineno))
                    candidates.update(
                        f'{child.module}.{alias.name}' for alias in child.names
                    )
            else:
                visit(child)

    visit(tree)
    return found, candidates


def _viewer_import_closure() -> dict[str, list[tuple[str, int]]]:
    """Map each module the viewer loads to its module-level imports."""
    pending = [
        f'anthias_viewer.{p.stem}'
        if p.stem != '__init__'
        else 'anthias_viewer'
        for p in sorted((SRC / 'anthias_viewer').rglob('*.py'))
    ]
    closure: dict[str, list[tuple[str, int]]] = {}
    while pending:
        module = pending.pop()
        if module in closure:
            continue
        path = _module_path(module)
        if path is None:
            continue
        imports, candidates = _module_level_imports(
            ast.parse(path.read_text())
        )
        closure[module] = imports
        pending.extend(
            name
            for name in {n for n, _ in imports} | candidates
            if name.startswith(FIRST_PARTY_ROOTS)
        )
    return closure


def test_the_closure_is_actually_walked() -> None:
    """Guard the guard: a resolver that silently found nothing would
    make the assertion below vacuously true."""
    closure = _viewer_import_closure()
    assert 'anthias_viewer' in closure
    # Reached only by following anthias_viewer -> anthias_server.
    assert 'anthias_server.lib.cec_client' in closure
    assert 'anthias_server.django_project.settings' in closure


def test_viewer_closure_does_not_import_celery() -> None:
    offenders = [
        f'{module}:{lineno} imports {name}'
        for module, imports in _viewer_import_closure().items()
        for name, lineno in imports
        if name.split('.')[0] in FORBIDDEN_ROOTS
    ]
    assert not offenders, (
        'These modules are loaded by the viewer, whose image installs '
        'only the `viewer` dependency group and therefore has no '
        'celery:\n  ' + '\n  '.join(offenders) + '\nMove the import '
        'inside the function that needs it, or match the exception by '
        'name+module the way _sentry_before_send does.'
    )
