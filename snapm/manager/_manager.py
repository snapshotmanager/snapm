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
from subprocess import run, CalledProcessError
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
    create_snapset_revert_entry,
    delete_snapset_revert_entry,
    check_boom_config,
)

from snapm import (
    SNAPM_DEBUG_MANAGER,
    SnapmError,
    SnapmCalloutError,
    SnapmNoSpaceError,
    SnapmNoProviderError,
    SnapmExistsError,
    SnapmBusyError,
    SnapmPathError,
    SnapmNotFoundError,
    SnapmInvalidIdentifierError,
    SnapmPluginError,
    SnapmStateError,
    Selection,
    is_size_policy,
    SnapStatus,
    SnapshotSet,
    Snapshot,
)


_log = logging.getLogger(__name__)
_log.set_debug_mask(SNAPM_DEBUG_MANAGER)

_log_debug = _log.debug
_log_debug_manager = _log.debug_masked
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error

JOURNALCTL_CMD = "journalctl"


class PluginRegistry(type):
    """
    Metaclass for Plugin classes.
    """

    plugins = []

    def __init__(cls, name, _bases, _attrs):
        super().__init__(name, _bases, _attrs)
        if name != "Plugin" and not name.startswith("_"):
            _log_debug("Loaded plugin %s version: %s", cls.name, cls.version)
            PluginRegistry.plugins.append(cls)


class Plugin(metaclass=PluginRegistry):
    """
    Abstract base class for snapshot manager plugins.
    """

    name = "plugin"
    version = "0.1.0"
    snapshot_class = Snapshot

    def __init__(self, logger):
        self.size_map = None
        self.logger = logger

    def _log_error(self, *args):
        """
        Log at error level.
        """
        self.logger.error(*args)

    def _log_warn(self, *args):
        """
        Log at warning level.
        """
        self.logger.warning(*args)

    def _log_info(self, *args):
        """
        Log at info level.
        """
        self.logger.info(*args)

    def _log_debug(self, *args):
        """
        Log at debug level.
        """
        self.logger.debug(*args)

    def info(self):
        """
        Return plugin name and version.
        """
        return {"name": self.name, "version": self.version}

    def start_transaction(self):
        """
        Begin a snapshot set creation transaction in this plugin.
        """
        self.size_map = {}

    def end_transaction(self):
        """
        End a snapshot set creation transaction in this plugin.
        """
        self.size_map = None

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

    def check_create_snapshot(
        self, origin, snapset_name, timestamp, mount_point, size_policy
    ):
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

    def create_snapshot(
        self, origin, snapset_name, timestamp, mount_point, size_policy
    ):
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

    def check_revert_snapshot(self, name, origin):
        """
        Check whether this snapshot can be reverted or not. This method returns
        if the current snapshot can be reverted and raises an exception if not.

        :returns: None
        :raises: ``NotImplementedError`` if this plugin does not support the
        revert operation, ``SnapmBusyError`` if the snapshot is already in the
        process of being reverted to another snapshot state or
        ``SnapmPluginError`` if another reason prevents the snapshot from being
        merged.
        """
        raise NotImplementedError

    def revert_snapshot(self, name):
        """
        Request to revert a snapshot and revert the content of the origin
        volume to its state at the time of the snapshot.

        This may be deferred until the next device activation or mount
        operation for the respective volume.

        :param name: The name of the snapshot to revert.
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


def _plugin_name(path):
    """
    Returns the plugin module name given the path.
    """
    base = os.path.basename(path)
    name, _ = os.path.splitext(base)
    return name


def _get_plugins_from_list(pluglist):
    """
    Get list of plugin names from file list.

    :param pluglist: list of candidate plugin files.
    """
    plugins = [
        _plugin_name(plugin)
        for plugin in pluglist
        if "__init__" not in plugin and plugin.endswith(".py")
    ]
    plugins.sort()
    return plugins


def _find_plugins_in_dir(path):
    """
    Find possible plugin files in ``path``.

    :param path: The search directory for plugin files.
    """
    _log_debug_manager("Finding plugins in %s", path)
    if os.path.exists(path):
        py_files = list(find("[a-zA-Z]*.py", path))
        pnames = _get_plugins_from_list(py_files)
        _log_debug_manager("Found plugin modules: %s", ", ".join(pnames))
        if pnames:
            return pnames
    return []


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

    def get_modules(self):
        """
        Get list of importable modules.

        Returns the list of importable modules in the configured python
        package.
        """
        plugins = []
        for path in self.package.__path__:
            if os.path.isdir(path):
                plugins.extend(_find_plugins_in_dir(path))

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
    try:
        module = __import__(module_fqname, globals(), locals(), [module_name])
    # pylint: disable=broad-except
    except Exception as err:
        _log_error("Error importing %s plugin module: %s", module_fqname, err)
        return []
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
    if select.uuid and select.uuid != snapshot_set.uuid:
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
    if select.snapshot_uuid and select.snapshot_uuid != snapshot.uuid:
        return False
    if select.snapshot_name and select.snapshot_name != snapshot.name:
        return False

    return True


def _suspend_journal():
    """
    Suspend journal writes to /var before creating snapshots.
    """
    try:
        run([JOURNALCTL_CMD, "--flush"], check=True)
        run([JOURNALCTL_CMD, "--relinquish-var"], check=True)
    except CalledProcessError as err:
        raise SnapmCalloutError(
            f"Error calling journalctl to flush journal: {err}"
        ) from err


def _resume_journal():
    """
    Resume journal writes to /var after creating snapshots.
    """
    try:
        run([JOURNALCTL_CMD, "--flush"], check=True)
    except CalledProcessError as err:
        raise SnapmCalloutError(
            f"Error calling journalctl to flush journal: {err}"
        ) from err


def _parse_mount_point_specs(mount_point_specs, default_size_policy):
    """
    Parse and normalize mount point paths and size policies.

    :param mount_point_specs: A list of mount points and optional size
                              policies.
    :returns: A tuple (mont_points, size_policies) containing a list of
              normalized mount points and a dictionary mapping mount
              points to size policies.
    """
    mount_points = []
    size_policies = {}

    # Parse size policies and normalise mount paths
    for mp_spec in mount_point_specs:
        if ":" in mp_spec:
            (mount, policy) = mp_spec.rsplit(":", maxsplit=1)
            if not is_size_policy(policy):
                mount = f"{mount}:{policy}"
                policy = default_size_policy
        else:
            (mount, policy) = (mp_spec, default_size_policy)
        mount = normpath(mount)
        mount_points.append(mount)
        size_policies[mount] = policy
    return (mount_points, size_policies)


def _check_revert_snapshot_set(snapset):
    """
    Check that all snapshots in a snapshot set can be reverted.
    Returns if all checks pass or raises an exception otherwise.

    :returns: None
    :raises: ``NotImplementedError``, ``SnapmBusyError`` or
             ``SnapmPluginError``
    """
    if snapset.status == SnapStatus.INVALID:
        raise SnapmStateError(
            f"Cannot revert snapset {snapset.name} with invalid snapshots"
        )

    # Check for ability to revert all snapshots in set
    for snapshot in snapset.snapshots:
        try:
            snapshot.check_revert()
        except NotImplementedError as err:
            _log_error(
                "Snapshot provider %s does not support revert",
                snapshot.provider,
            )
            raise SnapmPluginError from err
        except SnapmBusyError as err:
            _log_error(
                "Revert already in progress for snapshot origin %s",
                snapshot.origin,
            )
            raise SnapmBusyError(
                f"A revert has already started for snapshot set {snapset.name}"
            ) from err
        except SnapmPluginError as err:
            _log_error(
                "Revert prechecks failed for snapshot %s (%s)",
                snapshot.name,
                snapshot.provider,
            )
            raise err


def _check_snapset_status(snapset, operation):
    """
    Check that a snapshot set status is not ``SnapStatus.INVALID`` or
    ``SnapStatus.REVERTING`` before carrying out ``operation``.
    """
    if snapset.status in (SnapStatus.INVALID, SnapStatus.REVERTING):
        if snapset.status == SnapStatus.INVALID:
            _log_error("Cannot operate on invalid snapshot set '%s'", snapset.name)
        if snapset.status == SnapStatus.REVERTING:
            _log_error("Cannot operate on reverting snapshot set '%s'", snapset.name)
        raise SnapmStateError(f"Failed to {operation} snapset '{snapset.name}'")


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
        check_boom_config()
        self._boot_cache = BootCache()
        load_plugins()
        for plugin_class in PluginRegistry.plugins:
            try:
                plugin = plugin_class(_log)
                self.plugins.append(plugin)
            except SnapmNotFoundError as err:
                _log_debug(
                    "Plugin dependencies missing: %s (%s), skipping.",
                    plugin_class.__name__,
                    err,
                )
            except SnapmPluginError as err:
                _log_error("Disabling plugin %s: %s", plugin_class.__name__, err)
        self.discover_snapshot_sets()

    def _find_and_verify_plugins(self, mount_points, size_policies):
        """
        Find snapshot provider plugins for each mount point in ``mount_points``
        and verify that a provider exists for each mount present.

        :param mount_points: A list of mount points.
        :param size_policies: A dictionary mapping mount points to size policies.
        :returns: A dictionary mapping mount points to plugins.
        """
        # Initialise provider mapping.
        provider_map = {k: None for k in mount_points}

        # Find provider plugins for mount points
        for mount in mount_points:
            _log_debug(
                "Probing plugins for %s with size policy %s",
                mount,
                size_policies[mount],
            )

            if not ismount(mount):
                raise SnapmPathError(f"Path '{mount}' is not a mount point")

            for plugin in self.plugins:
                if plugin.can_snapshot(mount):
                    provider_map[mount] = plugin

        # Verify each mount point has a provider plugin
        for mount in provider_map:
            if provider_map[mount] is None:
                raise SnapmNoProviderError(
                    f"Could not find snapshot provider for {mount}"
                )
        return provider_map

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

            # Associate snapset with revert entry if present
            if snapset.name in self._boot_cache.revert_cache:
                snapset.revert_entry = self._boot_cache.revert_cache[snapset.name]
            elif str(snapset.uuid) in self._boot_cache.revert_cache:
                snapset.revert_entry = self._boot_cache.revert_cache[str(snapset.uuid)]

            self.snapshot_sets.append(snapset)
            self.by_name[snapset.name] = snapset
            self.by_uuid[snapset.uuid] = snapset
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

    def create_snapshot_set(self, name, mount_point_specs, default_size_policy=None):
        """
        Create a snapshot set of the supplied mount points with the name
        ``name``.

        :param name: The name of the snapshot set.
        :param mount_points: A list of mount point paths to include in the set.
        :raises: ``SnapmExistsError`` if the name is already in use, or
                 ``SnapmInvalidIdentifierError`` if the name fails validation.
        """
        self._validate_snapset_name(name)

        # Parse size policies and normalise mount paths
        (mount_points, size_policies) = _parse_mount_point_specs(
            mount_point_specs, default_size_policy
        )

        # Initialise provider mapping.
        provider_map = self._find_and_verify_plugins(mount_points, size_policies)

        for provider in set(provider_map.values()):
            provider.start_transaction()

        timestamp = floor(time())
        origins = {}
        for mount in provider_map:
            origins[mount] = provider_map[mount].origin_from_mount_point(mount)
            try:
                provider_map[mount].check_create_snapshot(
                    origins[mount], name, timestamp, mount, size_policies[mount]
                )
            except SnapmInvalidIdentifierError as err:
                _log_error(
                    "Error creating %s snapshot: %s", provider_map[mount].name, err
                )
                raise SnapmInvalidIdentifierError(
                    f"Snapset name {name} too long"
                ) from err
            except SnapmNoSpaceError as err:
                _log_error(
                    "Error creating %s snapshot: %s", provider_map[mount].name, err
                )
                raise SnapmNoSpaceError(
                    f"Insufficient free space for snapshot set {name}"
                ) from err

        _suspend_journal()
        snapshots = []
        for mount in provider_map:
            try:
                snapshots.append(
                    provider_map[mount].create_snapshot(
                        origins[mount], name, timestamp, mount, size_policies[mount]
                    )
                )
            except SnapmError as err:
                _log_error("Error creating snapshot set member %s: %s", name, err)
                for snapshot in snapshots:
                    snapshot.delete()
                raise SnapmPluginError(
                    f"Could not create all snapshots for set {name}"
                ) from err
        _resume_journal()

        for provider in set(provider_map.values()):
            provider.end_transaction()

        snapset = SnapshotSet(name, timestamp, snapshots)
        for snapshot in snapset.snapshots:
            snapshot.snapshot_set = snapset
        self.by_name[snapset.name] = snapset
        self.by_uuid[snapset.uuid] = snapset
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
        _check_snapset_status(snapset, "rename")

        snapshots = snapset.snapshots
        timestamp = snapset.timestamp
        new_snapshots = []

        # Remove references to old set
        self.snapshot_sets.remove(snapset)
        self.by_name.pop(snapset.name)
        self.by_uuid.pop(snapset.uuid)

        for snapshot in snapshots.copy():
            snapshots.remove(snapshot)
            try:
                new_snapshot = snapshot.rename(new_name)
                new_snapshots.append(new_snapshot)
            except SnapmError as err:
                _log_error("Failed to rename snapshot %s: %s", snapshot.name, err)
                rollback_snapshots = []
                for new_snapshot in new_snapshots:
                    try:
                        rollback_snapshot = snapshot.rename(old_name)
                        rollback_snapshots.append(rollback_snapshot)
                    except SnapmError as err2:
                        _log_error(
                            "Failed to rollback snapshot rename on %s: %s",
                            snapshot.name,
                            err2,
                        )
                old_snapset = SnapshotSet(
                    old_name, timestamp, snapshots + rollback_snapshots
                )
                self.by_name[old_snapset.name] = old_snapset
                self.by_uuid[old_snapset.uuid] = old_snapset
                self.snapshot_sets.append(old_snapset)
                raise SnapmPluginError(
                    f"Could not rename all snapshots for set {old_name}"
                ) from err

        new_snapset = SnapshotSet(new_name, timestamp, new_snapshots)
        for snapshot in new_snapset.snapshots:
            snapshot.snapshot_set = new_snapset
        self.by_name[new_snapset.name] = new_snapset
        self.by_uuid[new_snapset.uuid] = new_snapset
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
            if any(snapshot.snapshot_mounted for snapshot in snapset.snapshots):
                raise SnapmBusyError(
                    f"Snapshots from snapshot set {snapset.name} are mounted: cannot delete"
                )
            delete_snapset_boot_entry(snapset)
            delete_snapset_revert_entry(snapset)
            for snapshot in snapset.snapshots:
                try:
                    snapshot.delete()
                except SnapmError as err:
                    _log_error(
                        "Failed to delete snapshot set member %s: %s",
                        snapshot.name,
                        err,
                    )
                    raise SnapmPluginError(
                        f"Could not delete all snapshots for set {snapset.name}"
                    ) from err

            self.snapshot_sets.remove(snapset)
            self.by_name.pop(snapset.name)
            self.by_uuid.pop(snapset.uuid)
            deleted += 1
        self._boot_cache.refresh_cache()
        return deleted

    def revert_snapshot_set(self, name=None, uuid=None):
        """
        Revert snapshot sets matching selection criteria ``selection``.

        Request to revert each snapshot origin within each snapshot set
        to the state at the time the snapshot was taken.

        :param selection: Selection criteria for snapshot sets to revert.
        """
        if name and uuid:
            if self.by_name[name] != self.by_uuid[uuid]:
                raise SnapmInvalidIdentifierError(
                    f"Conflicting name and UUID: {str(uuid)} does not match '{name}'"
                )
            snapset = self.by_name[name]
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

        _check_revert_snapshot_set(snapset)

        # Snapshot boot entry becomes invalid as soon as revert is initiated.
        delete_snapset_boot_entry(snapset)

        revert_entry = snapset.revert_entry
        mounted = snapset.mounted
        name = snapset.name

        # Perform revert operation on all snapshots
        for snapshot in snapset.snapshots:
            try:
                snapshot.revert()
            except SnapmError as err:
                _log_error(
                    "Failed to revert snapshot set member %s: %s",
                    snapshot.name,
                    err,
                )
                raise SnapmPluginError(
                    f"Could not revert all snapshots for set {snapset.name}"
                ) from err
        if mounted:
            _log_warn(
                "Snaphot set %s is in use: reboot required to complete revert",
                name,
            )
            if revert_entry:
                _log_warn(
                    "Boot into '%s' to continue",
                    revert_entry.title,
                )

        self._boot_cache.refresh_cache()
        return snapset

    def revert_snapshot_sets(self, selection):
        """
        Revert snapshot sets matching selection criteria ``selection``.

        Request to revert each snapshot origin within each snapshot set
        to the state at the time the snapshot was taken.

        :param selection: Selection criteria for snapshot sets to revert.
        """
        sets = self.find_snapshot_sets(selection=selection)
        reverted = 0
        if not sets:
            raise SnapmNotFoundError(
                f"Could not find snapshot sets matching {selection}"
            )
        for snapset in sets:
            self.revert_snapshot_set(name=snapset.name)
            reverted += 1
        return reverted

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
            _check_snapset_status(snapset, "activate")

            for snapshot in snapset.snapshots:
                try:
                    snapshot.activate()
                except SnapmError as err:
                    _log_error(
                        "Failed to activate snapshot set member %s: %s",
                        snapshot.name,
                        err,
                    )
                    raise SnapmPluginError(
                        f"Could not activate all snapshots for set {snapset.name}"
                    ) from err
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
            _check_snapset_status(snapset, "deactivate")

            for snapshot in snapset.snapshots:
                try:
                    snapshot.deactivate()
                except SnapmError as err:
                    _log_error(
                        "Failed to deactivate snapshot set member %s: %s",
                        snapshot.name,
                        err,
                    )
                    raise SnapmPluginError(
                        f"Could not deactivate all snapshots for set {snapset.name}"
                    ) from err
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
            _check_snapset_status(snapset, "set autoactivate status")
            snapset.autoactivate = auto
            changed += 1
        return changed

    def create_snapshot_set_boot_entry(self, name=None, uuid=None):
        """
        Create a snapshot boot entry for the specified snapshot set.

        :param name: The name of the snapshot set.
        :param uuid: The UUID of the snapshot set.
        """
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

        snapset.autoactivate = True
        if not snapset.autoactivate:
            raise SnapmPluginError(
                "Could not enable autoactivation for all snapshots in snapshot "
                f"set {snapset.name}"
            )
        if snapset.boot_entry is not None:
            raise SnapmExistsError(
                f"Boot entry already associated with snapshot set {snapset.name}"
            )
        create_snapset_boot_entry(snapset)
        self._boot_cache.refresh_cache()

    def create_snapshot_set_revert_entry(self, name=None, uuid=None):
        """
        Create a revert boot entry for the specified snapshot set.

        :param name: The name of the snapshot set.
        :param uuid: The UUID of the snapshot set.
        """
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

        if snapset.revert_entry is not None:
            raise SnapmExistsError(
                f"Revert entry already associated with snapshot set {snapset.name}"
            )
        create_snapset_revert_entry(snapset)
        self._boot_cache.refresh_cache()


__all__ = [
    "Plugin",
    "Manager",
]
