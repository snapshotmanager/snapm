# Copyright Red Hat
#
# snapm/manager/_loader.py - Snapshot Manager plugin loader
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: GPL-2.0-only
"""
Plugin loader logic for Snapshot Manager.
"""
import inspect
import importlib
import logging
from pathlib import Path
from importlib.util import find_spec
from snapm.manager.plugins import Plugin
import snapm.manager.plugins as plugin_pkg

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


def _find_plugin_modules(path: Path):
    for file in path.glob("[a-zA-Z]*.py"):
        if not file.name.startswith("_") and file.name != "__init__.py":
            yield file.stem


def _import_plugin_module(fqname: str):
    if not find_spec(fqname):
        return None
    try:
        _log_debug("Importing plugin module %s", fqname)
        return importlib.import_module(fqname)
    except (ModuleNotFoundError, ImportError) as e:
        _log_error("Error importing plugin %s: %s", fqname, e)
        return None


def _find_plugins_in_module(module, base_class):
    members = inspect.getmembers(module, inspect.isclass)
    names_to_check = getattr(module, "__all__", [name for name, _ in members])

    return [
        cls
        for name, cls in members
        if name in names_to_check
        and not name.startswith("_")
        and issubclass(cls, base_class)
        and cls is not base_class
        and cls.__module__ == module.__name__
    ]


def load_plugins(base_class=Plugin):
    """
    Attempt to load public plugins from ``plugin_pkg`` that are subclmsses of
    the class ``base_class``.
    """
    found_plugins = []

    for path in map(Path, plugin_pkg.__path__):
        for module_name in _find_plugin_modules(path):
            fqname = f"{plugin_pkg.__name__}.{module_name}"
            module = _import_plugin_module(fqname)
            if module:
                found_plugins.extend(_find_plugins_in_module(module, base_class))

    return sorted(found_plugins, key=lambda cls: cls.__name__)
