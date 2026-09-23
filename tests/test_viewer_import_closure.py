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
run on a code path the viewer does not take. Everything else that the
import statement actually executes is in scope, and Copilot's review
found four ways the first cut of this walker let something through:

* class bodies, which run at class-creation time during the import;
* ``from . import helper``, which executes ``helper.py`` just as an
  absolute import would;
* the package ``__init__.py`` files along the way — importing
  ``anthias_server.lib.cec_client`` runs ``anthias_server/lib/
  __init__.py`` first;
* modules in subpackages, whose dotted name is not just their filename.

All four are covered below. A guard that silently walks less than it
claims is worse than no guard, so ``test_the_closure_is_actually_walked``
pins the shape of the result and the unit tests pin each rule.
"""

import ast
from pathlib import Path

import pytest

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


def _module_name(path: Path) -> str:
    """Dotted name for a file under ``src/``.

    Derived from the full path rather than the filename, so a module in
    a subpackage gets ``pkg.sub.mod`` and not ``pkg.mod`` — the latter
    resolves to nothing and would drop the module from the walk.
    """
    parts = list(path.relative_to(SRC).with_suffix('').parts)
    if parts[-1] == '__init__':
        parts.pop()
    return '.'.join(parts)


def _package_of(module: str, path: Path) -> str:
    """The package a relative import inside ``module`` is relative to.

    For a package's own ``__init__.py`` that is the package itself; for
    an ordinary module it is the parent.
    """
    if path.name == '__init__.py':
        return module
    return module.rpartition('.')[0]


def _resolve_relative(package: str, level: int, module: str | None) -> str:
    """Absolute name for a ``from .x import y`` inside ``package``.

    ``level`` is the number of leading dots: one means "this package",
    two means "the parent", and so on. Returns '' when the dots climb
    past the top of the tree, which cannot resolve to anything here.
    """
    parts = package.split('.') if package else []
    climb = level - 1
    if climb > len(parts):
        return ''
    base = parts[: len(parts) - climb] if climb else parts
    if module:
        base = [*base, *module.split('.')]
    return '.'.join(base)


def _ancestor_packages(module: str) -> list[str]:
    """Every package whose ``__init__.py`` runs before ``module`` does.

    ``import anthias_server.lib.cec_client`` executes
    ``anthias_server/__init__.py`` and ``anthias_server/lib/__init__.py``
    on the way, so a celery import in either crashes the viewer just as
    surely as one in the leaf module.
    """
    parts = module.split('.')
    return ['.'.join(parts[:i]) for i in range(1, len(parts))]


def _module_level_imports(
    tree: ast.Module, package: str = ''
) -> tuple[list[tuple[str, int]], set[str]]:
    """``(imported_modules, follow_candidates)`` outside any function.

    Everything that runs during the import counts: module-level
    ``try``/``if`` blocks, and class bodies too — ``class Foo: import
    celery`` executes at class-creation time and would crash the viewer
    just the same. Only ``def``/``async def`` bodies are skipped, since
    those run later or not at all.

    ``from pkg import a, b`` names ``pkg`` for the forbidden-root check,
    but ``a``/``b`` may themselves be submodules to descend into — the
    form ``from anthias_server.lib import cec_client`` is how the viewer
    reaches half of its closure. Those go in the second set, where a
    name that turns out to be an ordinary attribute simply fails to
    resolve to a file and is dropped.

    Relative imports are resolved against ``package`` and go in the same
    set. They are first-party by construction so they never trip the
    forbidden-root check themselves, but ``from . import helper`` runs
    ``helper.py``, and a celery import *there* is exactly the crash this
    guard exists to catch.
    """
    found: list[tuple[str, int]] = []
    candidates: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if isinstance(child, ast.Import):
                found.extend(
                    (alias.name, child.lineno) for alias in child.names
                )
            elif isinstance(child, ast.ImportFrom):
                if child.level:
                    base = _resolve_relative(
                        package, child.level, child.module
                    )
                    if base:
                        candidates.add(base)
                        candidates.update(
                            f'{base}.{alias.name}' for alias in child.names
                        )
                elif child.module:
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
        _module_name(p) for p in sorted((SRC / 'anthias_viewer').rglob('*.py'))
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
            ast.parse(path.read_text()), _package_of(module, path)
        )
        closure[module] = imports
        # Ancestor packages run their __init__.py before this module's
        # body does, so they are part of the closure even when nothing
        # imports them by name.
        reachable = (
            {n for n, _ in imports}
            | candidates
            | set(_ancestor_packages(module))
        )
        pending.extend(
            name for name in reachable if name.startswith(FIRST_PARTY_ROOTS)
        )
    return closure


def test_walker_counts_everything_that_runs_at_import() -> None:
    """The scoping rule is the whole substance of this guard, so pin it
    directly rather than only through the real tree.

    The class-body case is the one that was wrong first time round
    (Copilot): a class body executes while the module is imported, so
    skipping it would let a crash-on-startup import through unseen.
    """
    source = """
import celery.top
from billiard.a import thing

try:
    import kombu.guarded
except ImportError:
    pass

if True:
    import celery.conditional


class Holder:
    import celery.in_class_body


def later():
    import celery.in_function


async def later_async():
    import celery.in_async_function


class WithMethod:
    def method(self):
        import celery.in_method
"""
    found, _ = _module_level_imports(ast.parse(source))
    names = {name for name, _ in found}
    assert names == {
        'celery.top',
        'billiard.a',
        'kombu.guarded',
        'celery.conditional',
        'celery.in_class_body',
    }


def test_module_names_survive_subpackages() -> None:
    """A module in a subpackage is ``pkg.sub.mod``. Naming it after its
    filename alone resolves to nothing, and the module drops out of the
    walk unnoticed (Copilot)."""
    assert (
        _module_name(SRC / 'anthias_server' / 'lib' / 'cec_client.py')
        == 'anthias_server.lib.cec_client'
    )
    # A package is named for its directory, not for '__init__'.
    assert (
        _module_name(SRC / 'anthias_server' / 'lib' / '__init__.py')
        == 'anthias_server.lib'
    )


@pytest.mark.parametrize(
    ('package', 'level', 'module', 'expected'),
    [
        # `from . import helper` inside anthias_server.lib
        ('anthias_server.lib', 1, None, 'anthias_server.lib'),
        # `from .cec_client import available`
        (
            'anthias_server.lib',
            1,
            'cec_client',
            'anthias_server.lib.cec_client',
        ),
        # Two dots is the *parent* package, not the root:
        # `from ..base import X` inside lib.integrations is lib.base.
        (
            'anthias_server.lib.integrations',
            2,
            'base',
            'anthias_server.lib.base',
        ),
        # Three dots to climb two levels and reach the root's sibling.
        (
            'anthias_server.lib.integrations',
            3,
            'settings',
            'anthias_server.settings',
        ),
        # Dots that climb past the top resolve to nothing.
        ('anthias_server', 4, 'x', ''),
    ],
)
def test_relative_imports_resolve_against_their_package(
    package: str, level: int, module: str | None, expected: str
) -> None:
    """Discarding relative imports made this an incomplete walk: `from
    . import helper` executes helper.py, so a celery import there would
    crash the viewer while the guard passed (Copilot)."""
    assert _resolve_relative(package, level, module) == expected


def test_relative_imports_are_followed() -> None:
    source = 'from . import helper\nfrom .deep.mod import thing\n'
    _, candidates = _module_level_imports(
        ast.parse(source), 'anthias_server.lib'
    )
    assert 'anthias_server.lib.helper' in candidates
    assert 'anthias_server.lib.deep.mod' in candidates


def test_ancestor_packages_are_part_of_the_closure() -> None:
    """Importing a leaf runs every ``__init__.py`` above it first, so a
    celery import in one of those crashes the viewer too (Copilot)."""
    assert _ancestor_packages('anthias_server.lib.cec_client') == [
        'anthias_server',
        'anthias_server.lib',
    ]
    # And the real walk picks the package up even though nothing
    # imports `anthias_server.lib` by name.
    assert 'anthias_server.lib' in _viewer_import_closure()


def test_the_closure_is_actually_walked() -> None:
    """Guard the guard: a resolver that silently found nothing would
    make the assertions below vacuously true."""
    closure = _viewer_import_closure()
    assert 'anthias_viewer' in closure
    # Reached only by following anthias_viewer -> anthias_server.
    assert 'anthias_server.lib.cec_client' in closure
    assert 'anthias_server.django_project.settings' in closure
    # Reached only via `from anthias_server.lib import cec, cec_client`,
    # i.e. the submodule-of-a-from-import path.
    assert 'anthias_server.lib.cec' in closure
    # Package initializers, reached by no import statement at all.
    assert 'anthias_server.lib' in closure


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
