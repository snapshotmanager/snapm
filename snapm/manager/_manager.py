# Copyright (C) 2023 Red Hat, Inc., Bryn M. Reeves <bmr@redhat.com>
#
# snapm/manager/_manager.py - Snapshot Manager
#
# This file is part of the snapm project.
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions
# of the GNU General Public License v.2.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
"""
Manager interface and plugin infrastructure.
"""
from uuid import UUID
import logging
from time import time
from math import floor
from os.path import ismount, normpath
import fnmatch
import inspect
import os

import snapm.manager.plugins
from snapm.manager.boot import (
    BootCache,
    create_snapset_boot_entry,
    delete_snapset_boot_entry,
    create_snapset_rollback_entry,
    delete_snapset_rollback_entry,
)

from snapm import (
    SNAPM_DEBUG_MANAGER,
    SnapmError,
    SnapmNoProviderError,
    SnapmExistsError,
    SnapmPathError,
    SnapmNotFoundError,
    SnapmInvalidIdentifierError,
    Selection,
    SnapshotSet,
)


_log = logging.getLogger(__name__)
_log.set_debug_mask(SNAPM_DEBUG_MANAGER)

_log_debug = _log.debug
_log_debug_manager = _log.debug_masked
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


class PluginRegistry(type):
    """
    Metaclass for Plugin classes.
    """

    plugins = []

    def __init__(cls, name, _bases, _attrs):
        if name != "Plugin" and not name.startswith("_"):
            _log_debug("Loaded plugin %s version: %s", cls.name, cls.version)
            PluginRegistry.plugins.append(cls)


class Plugin(metaclass=PluginRegistry):
    """
    Abstract base class for snapshot manager plugins.
    """

    name = "plugin"
    version = "0.1.0"

    def info(self):
        """
        Return plugin name and version.
        """
        return {"name": self.name, "version": self.version}

    def discover_snapshots(self):
        """
        Discover snapshots managed by this plugin class.

        Returns a list of objects that are a subclass of ``Snapshot``.
        :returns: A list of snapshots discovered by this plugin class.
        """
        raise NotImplementedError

    def can_snapshot(self, mount_point):
        """
        Test whether this plugin can snapshot the specified mount point.

        :param mount_point: The mount point path to test.
        :returns: ``True`` if this plugin can snapshot the file system mounted
                  at ``mount_point``, or ``False`` otherwise.
        """
        raise NotImplementedError

    def check_create_snapshot(self, origin, snapset_name, timestamp, mount_point):
        """
        Perform pre-creation checks before creating a snapshot.

        :param origin: The origin volume for the snapshot.
        :param snapset_name: The name of the snapshot set to be created.
        :param timestamp: The snapshot set timestamp.
        :param mount_point: The mount point path for this snapshot.
        :raises: ``SnapmNoSpaceError`` if there is insufficient free space to
                 create the snapshot.
        """
        raise NotImplementedError

    def create_snapshot(self, origin, snapset_name, timestamp, mount_point):
        """
        Create a snapshot of ``origin`` in the snapset named ``snapset_name``.

        :param origin: The origin volume for the snapshot.
        :param snapset_name: The name of the snapshot set to be created.
        :param timestamp: The snapshot set timestamp.
        :param mount_point: The mount point path for this snapshot.
        :raises: ``SnapmNoSpaceError`` if there is insufficient free space to
                 create the snapshot.
        """
        raise NotImplementedError

    def rename_snapshot(self, old_name, origin, snapset_name, timestamp, mount_point):
        """
        Rename the snapshot named ``old_name`` according to the provided
        snapshot field values.

        :param old_name: The original name of the snapshot to be renamed.
        :param origin: The origin volume for the snapshot.
        :param snapset_name: The new name of the snapshot set.
        :param timestamp: The snapshot set timestamp.
        :param mount_point: The mount point of the snapshot.
        """
        raise NotImplementedError

    def rollback_snapshot(self, name):
        """
        Request to roll back a snapshot and revert the content of the origin
        volume to its state at the time of the snapshot.

        This may be deferred until the next device activation or mount
        operation for the respective volume.

        :param name: The name of the snapshot to roll back.
        """
        raise NotImplementedError

    def delete_snapshot(self, name):
        """
        Delete the snapshot named ``name``

        :param name: The name of the snapshot to be removed.
        """
        raise NotImplementedError

    def activate_snapshot(self, name):
        """
        Activate the snapshot named ``name``

        :param name: The name of the snapshot to be activated.
        """
        raise NotImplementedError

    def deactivate_snapshot(self, name):
        """
        Deactivate the snapshot named ``name``

        :param name: The name of the snapshot to be deactivated.
        """
        raise NotImplementedError

    def set_autoactivate(self, name, auto=False):
        """
        Set the autoactivation state of the snapshot named ``name``.

        :param name: The name of the snapshot to be modified.
        :param auto: ``True`` to enable autoactivation or ``False`` otherwise.
        """
        raise NotImplementedError

    def origin_from_mount_point(self, mount_point):
        """
        Return a string representing the origin from a given mount point path.

        :param mount_point: The mount point path.
        """
        raise NotImplementedError


def find(file_pattern, top_dir, max_depth=None, path_pattern=None):
    """
    Generator function to find files recursively.
    Usage::

        for filename in find("*.properties", "/var/log/foobar"):
            print filename
    """
    if max_depth:
        base_depth = os.path.dirname(top_dir).count(os.path.sep)
        max_depth += base_depth

    for path, dirlist, filelist in os.walk(top_dir):
        if max_depth and path.count(os.path.sep) >= max_depth:
            del dirlist[:]

        if path_pattern and not fnmatch.fnmatch(path, path_pattern):
            continue

        for name in fnmatch.filter(filelist, file_pattern):
            yield os.path.join(path, name)


class ImporterHelper:
    """
    Provides a list of modules that can be imported in a package.

    Importable modules are located along the module __path__ list and modules
    are files that end in .py.
    """

    def __init__(self, package):
        """
        package is a package module

        import my.package.module
        helper = ImporterHelper(my.package.module)
        """
        self.package = package

    def _plugin_name(self, path):
        """
        Returns the plugin module name given the path.
        """
        base = os.path.basename(path)
        name, _ = os.path.splitext(base)
        return name

    def _get_plugins_from_list(self, pluglist):
        plugins = [
            self._plugin_name(plugin)
            for plugin in pluglist
            if "__init__" not in plugin and plugin.endswith(".py")
        ]
        plugins.sort()
        return plugins

    def _find_plugins_in_dir(self, path):
        _log_debug_manager("Finding plugins in %s", path)
        if os.path.exists(path):
            py_files = list(find("[a-zA-Z]*.py", path))
            pnames = self._get_plugins_from_list(py_files)
            _log_debug_manager("Found plugin modules: %s", ", ".join(pnames))
            if pnames:
                return pnames
        return []

    def get_modules(self):
        """
        Get list of importable modules.

        Returns the list of importable modules in the configured python
        package.
        """
        plugins = []
        for path in self.package.__path__:
            if os.path.isdir(path):
                plugins.extend(self._find_plugins_in_dir(path))

        return plugins


def import_module(module_fqname, superclasses=None):
    """
    Import a single module.

    Imports the module module_fqname and returns a list of defined classes
    from that module. If superclasses is defined then the classes returned will
    be subclasses of the specified superclass or superclasses. If superclasses
    is plural it must be a tuple of classes.
    """
    module_name = module_fqname.rpartition(".")[-1]
    module = __import__(module_fqname, globals(), locals(), [module_name])
    modules = [
        class_
        for cname, class_ in inspect.getmembers(module, inspect.isclass)
        if class_.__module__ == module_fqname
    ]
    if superclasses:
        modules = [m for m in modules if issubclass(m, superclasses)]

    return modules


def import_plugin(name, superclasses=None):
    """
    Import name as a module and return a list of all classes defined in that
    module. superclasses should be a tuple of valid superclasses to import,
    this defaults to (Plugin,).
    """
    plugin_fqname = f"snapm.manager.plugins.{name}"
    if not superclasses:
        superclasses = (Plugin,)
    _log_debug("Importing plugin module %s", plugin_fqname)
    return import_module(plugin_fqname, superclasses)


def load_plugins():
    """
    Attempt to load plugin modules.
    """
    helper = ImporterHelper(snapm.manager.plugins)
    plugins = helper.get_modules()
    _log_debug(
        "Importing %d modules from %s", len(plugins), snapm.manager.plugins.__name__
    )
    for plug in plugins:
        plugbase, _ = os.path.splitext(plug)
        if not plugbase.startswith("_"):
            import_plugin(plugbase)


def select_snapshot_set(select, snapshot_set):
    """
    Test SnapshotSet against Selection criteria.

    Test the supplied ``SnapshotSet`` against the selection criteria
    in ``s`` and return ``True`` if it passes, or ``False``
    otherwise.

    :param s: The selection criteria
    :param be: The SnapshotSet to test
    :rtype: bool
    :returns: True if SnapshotSet passes selection or ``False``
              otherwise.
    """
    if select.name and select.name != snapshot_set.name:
        return False
    if select.uuid and UUID(select.uuid) != snapshot_set.uuid:
        return False
    if select.timestamp and select.timestamp != snapshot_set.timestamp:
        return False
    if select.nr_snapshots and select.nr_snapshots != snapshot_set.nr_snapshots:
        return False
    if select.mount_points:
        s_mount_points = sorted(select.mount_points)
        mount_points = sorted(snapshot_set.mount_points)
        if s_mount_points != mount_points:
            return False

    return True


def select_snapshot(select, snapshot):
    """
    Test SnapshotSet against Selection criteria.

    Test the supplied ``SnapshotSet`` against the selection criteria
    in ``s`` and return ``True`` if it passes, or ``False``
    otherwise.

    :param s: The selection criteria
    :param be: The SnapshotSet to test
    :rtype: bool
    :returns: True if SnapshotSet passes selection or ``False``
              otherwise.
    """
    if not select_snapshot_set(select, snapshot.snapshot_set):
        return False
    if select.snapshot_uuid and UUID(select.snapshot_uuid) != snapshot.uuid:
        return False
    if select.snapshot_name and select.snapshot_name != snapshot.name:
        return False

    return True


class Manager:
    """
    Snapshot Manager high level interface.
    """

    plugins = []
    snapshot_sets = []
    by_name = {}
    by_uuid = {}

    def __init__(self):
        self.plugins = []
        self.snapshot_sets = []
        self.by_name = {}
        self.by_uuid = {}
        self._boot_cache = BootCache()
        load_plugins()
        for plugin_class in PluginRegistry.plugins:
            self.plugins.append(plugin_class())
        self.discover_snapshot_sets()

    def discover_snapshot_sets(self):
        """
        Discover snapshot sets by calling into each plugin to find
        individual snapshots and then aggregating them together into
        snapshot sets.

        Initialises the ``snapshot_sets``, ``by_name`` and ``by_uuid`` members
        with the discovered snapshot sets.
        """
        self.snapshot_sets = []
        self.by_name = {}
        self.by_uuid = {}
        snapshots = []
        snapset_names = set()
        self._boot_cache.refresh_cache()
        _log_debug("Discovering snapshot sets for %s plugins", len(self.plugins))
        for plugin in self.plugins:
            snapshots.extend(plugin.discover_snapshots())
        _log_debug("Discovered %s managed snapshots", len(snapshots))
        for snapshot in snapshots:
            snapset_names.add(snapshot.snapset_name)
        for snapset_name in snapset_names:
            set_snapshots = [
                snap for snap in snapshots if snap.snapset_name == snapset_name
            ]
            set_timestamp = set_snapshots[0].timestamp
            for snap in set_snapshots:
                if snap.timestamp != set_timestamp:
                    _log_warn(
                        "Snapshot set '%s' has inconsistent timestamps", snapset_name
                    )
                    continue

            snapset = SnapshotSet(snapset_name, set_timestamp, set_snapshots)

            # Associate snapset with boot entry if present
            if snapset.name in self._boot_cache.entry_cache:
                snapset.boot_entry = self._boot_cache.entry_cache[snapset.name]
            elif str(snapset.uuid) in self._boot_cache.entry_cache:
                snapset.boot_entry = self._boot_cache.entry_cache[str(snapset.uuid)]

            # Associate snapset with rollback entry if present
            if snapset.name in self._boot_cache.rollback_cache:
                snapset.rollback_entry = self._boot_cache.rollback_cache[snapset.name]
            elif str(snapset.uuid) in self._boot_cache.rollback_cache:
                snapset.rollback_entry = self._boot_cache.rollback_cache[
                    str(snapset.uuid)
                ]

            self.snapshot_sets.append(snapset)
            self.by_name[snapset.name] = snapset
            self.by_uuid[str(snapset.uuid)] = snapset
            for snapshot in snapset.snapshots:
                snapshot.snapshot_set = snapset

        self.snapshot_sets.sort(key=lambda ss: ss.name)
        _log_debug("Discovered %d snapshot sets", len(self.snapshot_sets))

    def find_snapshot_sets(self, selection=None):
        """
        Find snapshot sets matching selection criteria.

        :param selection: Selection criteria to apply.
        :returns: A list of matching ``SnapshotSet`` objects.
        """
        matches = []

        # Use null selection criteria if unspecified
        selection = selection if selection else Selection()

        selection.check_valid_selection(snapshot_set=True)

        _log_debug("Finding snapshot sets for %s", repr(selection))

        for snapset in self.snapshot_sets:
            if select_snapshot_set(selection, snapset):
                matches.append(snapset)

        _log_debug("Found %d snapshot sets", len(matches))
        return matches

    def find_snapshots(self, selection=None):
        """
        Find snapshots matching selection criteria.

        :param selection: Selection criteria to apply.
        :returns: A list of matching ``Snapshot`` objects.
        """
        matches = []

        # Use null selection criteria if unspecified
        selection = selection if selection else Selection()

        selection.check_valid_selection(snapshot=True, snapshot_set=True)

        _log_debug("Finding snapshots for %s", repr(selection))

        for snapset in self.snapshot_sets:
            for snapshot in snapset.snapshots:
                if select_snapshot(selection, snapshot):
                    matches.append(snapshot)

        _log_debug("Found %d snapshots", len(matches))
        return matches

    def _validate_snapset_name(self, name):
        """
        Validate a snapshot set name.

        Returns if ``name`` is a valid snapshot set name or raises an
        appropriate exception otherwise.

        :param name: The snapshot set name to validate.
        :raises: ``SnapmExistsError`` if the name is already in use, or
                 ``SnapmInvalidIdentifierError`` if the name fails validation.
        """
        invalid_chars = ["/", "\\", "_", " "]
        if name in self.by_name:
            raise SnapmExistsError(f"Snapshot set named '{name}' already exists")
        for char in invalid_chars:
            if char in name:
                raise SnapmInvalidIdentifierError(
                    f"Snapshot set name cannot include '{char}'"
                )

    def create_snapshot_set(self, name, mount_points):
        """
        Create a snapshot set of the supplied mount points with the name
        ``name``.

        :param name: The name of the snapshot set.
        :param mount_points: A list of mount point paths to include in the set.
        :raises: ``SnapmExistsError`` if the name is already in use, or
                 ``SnapmInvalidIdentifierError`` if the name fails validation.
        """
        self._validate_snapset_name(name)

        # Normalise mount point paths and initialise provider mapping.
        mount_points = [normpath(path) for path in mount_points]
        provider_map = {k: None for k in mount_points}

        for mnt_pt in mount_points:
            if not ismount(mnt_pt):
                raise SnapmPathError(f"Path '{mnt_pt}' is not a mount point")
            for plugin in self.plugins:
                if plugin.can_snapshot(mnt_pt):
                    provider_map[mnt_pt] = plugin

        for mnt_pt in provider_map:
            if provider_map[mnt_pt] is None:
                raise SnapmNoProviderError(
                    f"Could not find snapshot provider for {mnt_pt}"
                )

        timestamp = floor(time())
        for mnt_pt in provider_map:
            origin = provider_map[mnt_pt].origin_from_mount_point(mnt_pt)
            provider_map[mnt_pt].check_create_snapshot(origin, name, timestamp, mnt_pt)

        snapshots = []
        for mnt_pt in provider_map:
            origin = provider_map[mnt_pt].origin_from_mount_point(mnt_pt)
            try:
                snapshots.append(
                    provider_map[mnt_pt].create_snapshot(
                        origin, name, timestamp, mnt_pt
                    )
                )
            except SnapmError as err:
                _log_error("Error creating snapshot set member %s: %s", name, err)
                for snapshot in snapshots:
                    snapshot.delete()
                raise err
        snapset = SnapshotSet(name, timestamp, snapshots)
        for snapshot in snapset.snapshots:
            snapshot.snapshot_set = snapset
        self.by_name[snapset.name] = snapset
        self.by_uuid[str(snapset.uuid)] = snapset
        self.snapshot_sets.append(snapset)
        return snapset

    def rename_snapshot_set(self, old_name, new_name):
        """
        Rename snapshot set ``old_name`` as ``new_name``.

        :param old_name: The name of the snapshot set to be renamed.
        :param new_name: The new name of the snapshot set.
        :raises: ``SnapmExistsError`` if the name is already in use, or
                 ``SnapmInvalidIdentifierError`` if the name fails validation.
        """
        if old_name not in self.by_name:
            raise SnapmNotFoundError(f"Cannot find snapshot set named {old_name}")

        self._validate_snapset_name(new_name)

        snapset = self.by_name[old_name]
        snapshots = snapset.snapshots
        timestamp = snapset.timestamp
        new_snapshots = []

        # Remove references to old set
        self.snapshot_sets.remove(snapset)
        self.by_name.pop(snapset.name)
        self.by_uuid.pop(str(snapset.uuid))

        for snapshot in snapshots.copy():
            snapshots.remove(snapshot)
            try:
                new_snapshot = snapshot.rename(new_name)
                new_snapshots.append(new_snapshot)
            except SnapmError as err:
                _log_error("Failed to rename snapshot %s: %s", snapshot.name, err)
                revert_snapshots = []
                for new_snapshot in new_snapshots:
                    try:
                        revert_snapshot = snapshot.rename(old_name)
                        revert_snapshots.append(revert_snapshot)
                    except SnapmError as err2:
                        _log_error(
                            "Failed to revert snapshot rename on %s: %s",
                            snapshot.name,
                            err2,
                        )
                old_snapset = SnapshotSet(
                    old_name, timestamp, snapshots + revert_snapshots
                )
                self.by_name[old_snapset.name] = old_snapset
                self.by_uuid[str(old_snapset.uuid)] = old_snapset
                self.snapshot_sets.append(old_snapset)
                raise err

        new_snapset = SnapshotSet(new_name, timestamp, new_snapshots)
        for snapshot in new_snapset.snapshots:
            snapshot.snapshot_set = new_snapset
        self.by_name[new_snapset.name] = new_snapset
        self.by_uuid[str(new_snapset.uuid)] = new_snapset
        self.snapshot_sets.append(new_snapset)
        return new_snapset

    def delete_snapshot_sets(self, selection):
        """
        Remove snapshot sets matching selection criteria ``selection``.

        :param selection: Selection criteria for snapshot sets to remove.
        """
        sets = self.find_snapshot_sets(selection=selection)
        deleted = 0
        if not sets:
            raise SnapmNotFoundError(
                f"Could not find snapshot sets matching {selection}"
            )
        for snapset in sets:
            delete_snapset_boot_entry(snapset)
            delete_snapset_rollback_entry(snapset)
            for snapshot in snapset.snapshots:
                try:
                    snapshot.delete()
                except SnapmError as err:
                    _log_error(
                        "Failed to delete snapshot set member %s: %s",
                        snapshot.name,
                        err,
                    )

            self.snapshot_sets.remove(snapset)
            self.by_name.pop(snapset.name)
            self.by_uuid.pop(str(snapset.uuid))
            deleted += 1
        return deleted

    def rollback_snapshot_sets(self, selection):
        """
        Roll back snapshot sets matching selection criteria ``selection``.

        Request to roll back each snapshot origin within each snapshot set
        to the state at the time the snapshot was taken.

        :param selection: Selection criteria for snapshot sets to roll back.
        """
        sets = self.find_snapshot_sets(selection=selection)
        rolled_back = 0
        if not sets:
            raise SnapmNotFoundError(
                f"Could not find snapshot sets matching {selection}"
            )
        for snapset in sets:
            for snapshot in snapset.snapshots:
                try:
                    snapshot.rollback()
                except SnapmError as err:
                    _log_error(
                        "Failed to roll back snapshot set member %s: %s",
                        snapshot.name,
                        err,
                    )
            rolled_back += 1
        return rolled_back

    def activate_snapshot_sets(self, selection):
        """
        Activate snapshot sets matching selection criteria ``selection``.

        :param selection: Selection criteria for snapshot sets to activate.
        """
        sets = self.find_snapshot_sets(selection=selection)
        activated = 0
        if not sets:
            raise SnapmNotFoundError(
                f"Could not find snapshot sets matching {selection}"
            )
        for snapset in sets:
            for snapshot in snapset.snapshots:
                try:
                    snapshot.activate()
                except SnapmError as err:
                    _log_error(
                        "Failed to activate snapshot set member %s: %s",
                        snapshot.name,
                        err,
                    )
            activated += 1
        return activated

    def deactivate_snapshot_sets(self, selection):
        """
        Deactivate snapshot sets matching selection criteria ``selection``.

        :param selection: Selection criteria for snapshot sets to deactivate.
        """
        sets = self.find_snapshot_sets(selection=selection)
        deactivated = 0
        if not sets:
            raise SnapmNotFoundError(
                f"Could not find snapshot sets matching {selection}"
            )
        for snapset in sets:
            for snapshot in snapset.snapshots:
                try:
                    snapshot.deactivate()
                except SnapmError as err:
                    _log_error(
                        "Failed to deactivate snapshot set member %s: %s",
                        snapshot.name,
                        err,
                    )
            deactivated += 1
        return deactivated

    def set_autoactivate(self, selection, auto=False):
        """
        Set autoactivation state for snapshot sets matching selection criteria
        ``selection``.

        :param selection: Selection criteria for snapshot sets to set
                          autoactivation.
        :param auto: ``True`` to enable autoactivation or ``False`` otherwise.
        """
        sets = self.find_snapshot_sets(selection=selection)
        changed = 0
        if not sets:
            raise SnapmNotFoundError(
                f"Could not find snapshot sets matching {selection}"
            )
        for snapset in sets:
            for snapshot in snapset.snapshots:
                try:
                    snapshot.set_autoactivate(auto=auto)
                except SnapmError as err:
                    _log_error(
                        "Failed to set autoactivation for snapshot set member %s: %s",
                        snapshot.name,
                        err,
                    )
            changed += 1
        return changed

    def create_snapshot_set_boot_entry(self, name=None, uuid=None):
        if name is not None:
            if name not in self.by_name:
                raise SnapmNotFoundError(f"Could not find snapshot set named {name}")
            snapset = self.by_name[name]
        elif uuid is not None:
            if uuid not in self.by_uuid:
                raise SnapmNotFoundError(
                    f"Could not find snapshot set with uuid {uuid}"
                )
            snapset = self.by_uuid[uuid]
        else:
            raise SnapmNotFoundError("A snapshot set name or UUID is required")

        if snapset.boot_entry is not None:
            raise SnapmExistsError(
                f"Boot entry already associated with snapshot set {snapset.name}"
            )
        create_snapset_boot_entry(snapset)

    def create_snapshot_set_rollback_entry(self, name=None, uuid=None):
        if name is not None:
            if name not in self.by_name:
                raise SnapmNotFoundError(f"Could not find snapshot set named {name}")
            snapset = self.by_name[name]
        elif uuid is not None:
            if uuid not in self.by_uuid:
                raise SnapmNotFoundError(
                    f"Could not find snapshot set with uuid {uuid}"
                )
            snapset = self.by_uuid[uuid]
        else:
            raise SnapmNotFoundError("A snapshot set name or UUID is required")

        if snapset.rollback_entry is not None:
            raise SnapmExistsError(
                f"Rollback entry already associated with snapshot set {snapset.name}"
            )
        create_snapset_rollback_entry(snapset)


__all__ = [
    "Plugin",
    "Manager",
]
