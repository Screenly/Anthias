"""Minimal ``imp`` shim for Python 3.12+ (the stdlib ``imp`` module was
removed in 3.12). Debian trixie ships Python 3.13, but Chromium 87's
build tooling (mojo codegen, bundled jinja2, build/print_python_deps.py)
still ``import imp``. This shim reimplements the small subset those tools
use on top of ``importlib`` so the Qt 5.15 WebEngine build runs on a
modern interpreter. build_qt5.sh copies this into python3's
site-packages (NOT onto a shared PYTHONPATH, which would also shadow
python2.7's real ``imp`` for the codegen actions that still run under
python2).
"""

import importlib
import importlib.machinery
import importlib.util
import os
import sys
import types

# Old imp.* file-type constants (values match CPython's historical ones).
PY_SOURCE = 1
PY_COMPILED = 2
C_EXTENSION = 3
PKG_DIRECTORY = 5
C_BUILTIN = 6
PY_FROZEN = 7
SEARCH_ERROR = 0


def new_module(name):
    return types.ModuleType(name)


def get_magic():
    return importlib.util.MAGIC_NUMBER


def cache_from_source(path, debug_override=None):
    return importlib.util.cache_from_source(path, debug_override=debug_override)


def source_from_cache(path):
    return importlib.util.source_from_cache(path)


def acquire_lock():
    pass


def release_lock():
    pass


def lock_held():
    return False


def get_suffixes():
    ext = [(s, 'rb', C_EXTENSION)
           for s in importlib.machinery.EXTENSION_SUFFIXES]
    src = [(s, 'r', PY_SOURCE)
           for s in importlib.machinery.SOURCE_SUFFIXES]
    byt = [(s, 'rb', PY_COMPILED)
           for s in importlib.machinery.BYTECODE_SUFFIXES]
    return ext + src + byt


def load_source(name, pathname, file=None):
    loader = importlib.machinery.SourceFileLoader(name, pathname)
    spec = importlib.util.spec_from_file_location(name, pathname, loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


def load_dynamic(name, pathname, file=None):
    loader = importlib.machinery.ExtensionFileLoader(name, pathname)
    spec = importlib.util.spec_from_file_location(name, pathname, loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


def find_module(name, path=None):
    if path is None:
        path = sys.path
    spec = importlib.machinery.PathFinder.find_spec(name, path)
    if spec is None:
        raise ImportError('No module named %r' % name, name=name)
    origin = spec.origin
    if spec.submodule_search_locations is not None:
        loc = list(spec.submodule_search_locations)
        return (None, loc[0] if loc else origin, ('', '', PKG_DIRECTORY))
    suffix = os.path.splitext(origin)[1] if origin else ''
    if suffix in importlib.machinery.EXTENSION_SUFFIXES:
        kind, mode = C_EXTENSION, 'rb'
    elif suffix in importlib.machinery.BYTECODE_SUFFIXES:
        kind, mode = PY_COMPILED, 'rb'
    else:
        kind, mode = PY_SOURCE, 'r'
    handle = open(origin, mode) if origin else None
    return (handle, origin, (suffix, mode, kind))


def load_module(name, file, pathname, description):
    _suffix, _mode, kind = description
    try:
        if kind == PY_SOURCE:
            return load_source(name, pathname, file)
        if kind == C_EXTENSION:
            return load_dynamic(name, pathname, file)
        if kind == PKG_DIRECTORY:
            init = os.path.join(pathname, '__init__.py')
            spec = importlib.util.spec_from_file_location(name, init)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module
    finally:
        if file:
            try:
                file.close()
            except Exception:
                pass
    return importlib.import_module(name)


def reload(module):
    return importlib.reload(module)


class NullImporter:
    def __init__(self, path):
        if path == '':
            raise ImportError('empty pathname', path='')
        if os.path.isdir(path):
            raise ImportError('existing directory', path=path)

    def find_module(self, fullname, path=None):
        return None
