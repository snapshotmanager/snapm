# Copyright Red Hat
#
# snapm/manager/_manager.py - Snapshot Manager
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Manager interface and plugin infrastructure.
"""
from subprocess import run, CalledProcessError
from dataclasses import dataclass, field
from configparser import ConfigParser
import logging
from time import time
from math import floor
from stat import S_ISBLK
from os import stat
from os.path import exists, ismount, join, normpath, samefile
from typing import List

from snapm import (
    SNAPM_DEBUG_MANAGER,
    SNAPM_VALID_NAME_CHARS,
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
    SnapmRecursionError,
    SnapmArgumentError,
    Selection,
    bool_to_yes_no,
    is_size_policy,
    SnapStatus,
    SnapshotSet,
)

from ._boot import (
    BootCache,
    create_snapset_boot_entry,
    delete_snapset_boot_entry,
    create_snapset_revert_entry,
    delete_snapset_revert_entry,
    check_boom_config,
)
from ._loader import load_plugins
from ._signals import suspend_signals


_log = logging.getLogger(__name__)
_log.set_debug_mask(SNAPM_DEBUG_MANAGER)

_log_debug = _log.debug
_log_debug_manager = _log.debug_masked
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error

JOURNALCTL_CMD = "journalctl"

#: Base directory for snapm configuration
_SNAPM_CFG_DIR = "/etc/snapm"

#: Main configuration file path
_SNAPM_CFG_PATH = join(_SNAPM_CFG_DIR, "snapm.conf")

#: Main configuration file section
_SNAPM_CFG_GLOBAL = "Global"

#: DisablePlugins configuration key
_SNAPM_CFG_DISABLE_PLUGINS = "DisablePlugins"

#: Path to directory for plugin configuration files
_PLUGINS_D_PATH = join(_SNAPM_CFG_DIR, "plugins.d")


@dataclass
class SnapmConfig:
    """
    Manager configuration.
    """

    disable_plugins: List[str] = field(default_factory=list)

    @classmethod
    def from_file(cls, config_file: str) -> "SnapmConfig":
        """
        Load ``SnapmConfig`` from an INI-style configuration file located at
        ``config_file``.

        :param config_file: path to snapm.conf
        :type config_file: ``str``.
        :returns: A ``SnapmConfig`` instance initialised from ``config_file``.
        :rtype: ``SnapmConfig``
        """
        disable_plugins = []

        if not exists(config_file):
            return SnapmConfig()

        _log_debug("Loading configuration from '%s'", config_file)
        cfg = ConfigParser()
        cfg.read([config_file])
        if cfg.has_section(_SNAPM_CFG_GLOBAL):
            if cfg.has_option(_SNAPM_CFG_GLOBAL, _SNAPM_CFG_DISABLE_PLUGINS):
                plugins = cfg[_SNAPM_CFG_GLOBAL][_SNAPM_CFG_DISABLE_PLUGINS]
                disable_plugins += [plug.strip() for plug in plugins.split(",")]

        return SnapmConfig(disable_plugins=disable_plugins)


# pylint: disable=too-many-return-statements
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
    if select.basename and select.basename != snapshot_set.basename:
        return False
    if select.index is not None and select.index != snapshot_set.index:
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
    except CalledProcessError as err:  # pragma: no cover
        raise SnapmCalloutError(
            f"Error calling journalctl to flush journal: {err}"
        ) from err


def _resume_journal():
    """
    Resume journal writes to /var after creating snapshots.
    """
    try:
        run([JOURNALCTL_CMD, "--flush"], check=True)
    except CalledProcessError as err:  # pragma: no cover
        raise SnapmCalloutError(
            f"Error calling journalctl to flush journal: {err}"
        ) from err


def _parse_source_specs(source_specs, default_size_policy):
    """
    Parse and normalize source paths and size policies.

    :param source_specs: A list of mount point or block device paths and
                         optional size policy strings.
    :returns: A tuple (sources, size_policies) containing a list of
              normalized mount point and block device paths and a dictionary
              mapping source paths to size policies.
    """
    sources = []
    size_policies = {}

    # Parse size policies and normalise mount paths
    for spec in source_specs:
        if ":" in spec:
            (source, policy) = spec.rsplit(":", maxsplit=1)
            if not is_size_policy(policy):
                source = f"{source}:{policy}"
                policy = default_size_policy
        else:
            (source, policy) = (spec, default_size_policy)
        source = normpath(source)
        sources.append(source)
        size_policies[source] = policy
    return (sources, size_policies)


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
                snapshot.provider.name,
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
                snapshot.provider.name,
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
        raise SnapmStateError(
            f"Failed to {operation} snapset '{snapset.name}': status is '{snapset.status}'"
        )


def _find_mount_point_for_devpath(devpath):
    """
    Return the first mount point found in /proc/mounts that corresponds to
    ``device``, or the empty string if no mount point can be found.
    """
    with open("/proc/mounts", "r", encoding="utf8") as mounts:
        for line in mounts:
            fields = line.split(" ")
            if exists(fields[0]):
                if samefile(devpath, fields[0]):
                    return fields[1]
    return ""


class Manager:
    """
    Snapshot Manager high level interface.
    """

    plugins = []
    snapshot_sets = []
    by_name = {}
    by_uuid = {}

    @suspend_signals
    def __init__(self):
        self.plugins = []
        self.snapshot_sets = []
        self.by_name = {}
        self.by_uuid = {}
        snapm_config = SnapmConfig.from_file(_SNAPM_CFG_PATH)
        self.disable_plugins = snapm_config.disable_plugins
        check_boom_config()
        self._boot_cache = BootCache()
        plugin_classes = load_plugins()
        for plugin_class in plugin_classes:
            if plugin_class.name in self.disable_plugins:
                _log_debug("Skipping diabled plugin '%s'", plugin_class.name)
                continue
            _log_debug("Loading plugin class '%s'", plugin_class.__name__)
            try:
                plugin_cfg = self._load_plugin_config(plugin_class.name)
                try:
                    plugin = plugin_class(_log, plugin_cfg)
                except TypeError as err:
                    _log_debug("Failed to load plugin '%s': %s", plugin_class.name, err)
                    continue
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

    def _load_plugin_config(self, plugin_name: str) -> ConfigParser:
        """
        Load optional configuration file for plugin ``plugin_name``.

        :param plugin_name: The name of the plugin to load config for.
        :type plugin_name: ``str``
        :returns: A (possibly empty) ``ConfigParser`` instance.
        :rtype: ``ConfigParser``
        """
        plugin_conf_file = join(_PLUGINS_D_PATH, f"{plugin_name}.conf")
        cfg = ConfigParser()

        if exists(plugin_conf_file):
            _log_debug("Loading plugin configuration from '%s'", plugin_conf_file)
            cfg.read([plugin_conf_file])

        return cfg

    def _find_and_verify_plugins(
        self, sources, size_policies, _requested_provider=None
    ):
        """
        Find snapshot provider plugins for each source in ``sources``
        and verify that a provider exists for each source present.

        :param sources: A list of source mount point or block device paths.
        :param size_policies: A dictionary mapping sources to size policies.
        :returns: A dictionary mapping sources to plugins.
        """
        # Initialise provider mapping.
        provider_map = {k: None for k in sources}

        # Find provider plugins for sources
        for source in sources:
            if not exists(source):
                _log_error("No such file or directory: %s", source)
                raise SnapmNotFoundError(f"Source path '{source}' does not exist")

            _log_debug(
                "Probing plugins for %s with size policy %s",
                source,
                size_policies[source],
            )

            if not ismount(source) and not S_ISBLK(stat(source).st_mode):
                raise SnapmPathError(
                    f"Path '{source}' is not a block device or mount point"
                )

            for plugin in self.plugins:
                if plugin.can_snapshot(source):
                    provider_map[source] = plugin

        # Verify each mount point has a provider plugin
        for source in provider_map:
            if provider_map[source] is None:
                raise SnapmNoProviderError(
                    f"Could not find snapshot provider for {source}"
                )
        return provider_map

    def _snapset_from_name_or_uuid(self, name=None, uuid=None):
        """
        Look a snapshot set up by ``name`` or ``uuid``. Returns a
        ``SnapshotSet`` correponding to either ``name`` or ``uuid`,
        or raises ``SnapmError`` on error.

        :param name: The name of the snapshot set to look up.
        :param uuid: The UUID of the snapshot set to look up.
        :returns: A ``SnapshotSet`` corresponding to the given name or UUID.
        :raises: ``SnapmNotFoundError`` is the name or UUID cannot be found.
                 ``SnapmInvalidIdentifierError`` if the name and UUID do not
                 match.
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

        if name and uuid:
            if self.by_name[name] != self.by_uuid[uuid]:
                raise SnapmInvalidIdentifierError(
                    f"Conflicting name and UUID: {str(uuid)} does not match '{name}'"
                )

        return snapset

    def _check_recursion(self, origins):
        """
        Verify that each entry in  ``origins`` corresponds to a device that is
        not a snapshot belonging to another snapshot set.

        :param origins: A list of origin devices to check.
        :raises: ``SnapmRecursionError`` if an origin device is a snapshot.
        """
        snapshot_devices = [
            snapshot.devpath
            for snapset in self.snapshot_sets
            for snapshot in snapset.snapshots
        ]
        for source, device in origins.items():
            if device in snapshot_devices:
                raise SnapmRecursionError(
                    "Snapshots of snapshots are not supported: "
                    f"{source} corresponds to snapshot device {device}"
                )

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
            try:
                snapshots.extend(plugin.discover_snapshots())
            # pylint: disable=broad-except
            except (Exception, SnapmError) as err:
                _log_warn(
                    "Snapshot discovery failed for plugin '%s': %s", plugin.name, err
                )
                continue
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

    def _find_next_index(self, basename):
        """
        Find the next index value for the recurring snapset with basename
        ``basename``.

        :param basename: The basename of the recurring snapset
        :returns: An integer index value
        """
        sets = self.find_snapshot_sets(selection=Selection(basename=basename))
        sets.sort(key=lambda x: x.index)
        return (sets[-1].index + 1) if sets else 0

    def _validate_snapset_name(self, name):
        """
        Validate a snapshot set name.

        Returns if ``name`` is a valid snapshot set name or raises an
        appropriate exception otherwise.

        :param name: The snapshot set name to validate.
        :raises: ``SnapmExistsError`` if the name is already in use, or
                 ``SnapmInvalidIdentifierError`` if the name fails validation.
        """
        if name in self.by_name:
            raise SnapmExistsError(f"Snapshot set named '{name}' already exists")
        for char in name:
            # Underscore is specifically disallowed in snapset names.
            if char == "_" or char not in SNAPM_VALID_NAME_CHARS:
                raise SnapmInvalidIdentifierError(
                    f"Snapshot set name cannot include '{char}'"
                )

    # pylint: disable=too-many-branches,too-many-locals,too-many-statements
    @suspend_signals
    def create_snapshot_set(
        self,
        name,
        source_specs,
        default_size_policy=None,
        boot=False,
        revert=False,
        autoindex=False,
    ):
        """
        Create a snapshot set of the supplied mount points with the name
        ``name``.

        :param name: The name of the snapshot set.
        :param source_specs: A list of mount point and block device paths to
                             include in the set.
        :param default_size_policy: A default size policy to use for the set.
        :param boot: Create a snapshot boot entry for this snapshot set.
        :param revert: Create a revert boot entry for this snapshot set.
        :param autoindex: Treat `name` as the basename of a recurring snapshot set
                          and generate and append an appropriate index value.
        :raises: ``SnapmExistsError`` if the name is already in use, or
                 ``SnapmInvalidIdentifierError`` if the name fails validation.
        """
        self._validate_snapset_name(name)

        if autoindex:
            index = self._find_next_index(name)
            name = f"{name}.{index}"

        _log_debug(
            "Attempting to create snapshot set '%s' from source specs %s",
            name,
            ", ".join(source_specs),
        )

        _log_debug(
            "Create arguments: default_size_policy=%s boot=%s revert=%s autoindex=%s",
            default_size_policy,
            boot,
            revert,
            autoindex,
        )

        # Parse size policies and normalise mount paths
        (sources, size_policies) = _parse_source_specs(
            source_specs, default_size_policy
        )

        # Initialise provider mapping.
        provider_map = self._find_and_verify_plugins(sources, size_policies, None)

        for provider in set(provider_map.values()):
            provider.start_transaction()

        timestamp = floor(time())
        origins = {}
        mounts = {}

        for source in provider_map:
            if S_ISBLK(stat(source).st_mode):
                mounts[source] = _find_mount_point_for_devpath(source)
                origins[source] = source
                mount = mounts[source]
                if mount in provider_map:
                    raise SnapmInvalidIdentifierError(
                        f"Duplicate snapshot source {source} already added to {name} as {mount}"
                    )
            else:
                mount = source
                origins[source] = provider_map[source].origin_from_mount_point(mount)

            try:
                provider_map[source].check_create_snapshot(
                    origins[source], name, timestamp, mount, size_policies[source]
                )
            except SnapmInvalidIdentifierError as err:
                _log_error(
                    "Error creating %s snapshot: %s", provider_map[source].name, err
                )
                raise SnapmInvalidIdentifierError(
                    f"Snapset name {name} too long"
                ) from err
            except SnapmNoSpaceError as err:
                _log_error(
                    "Error creating %s snapshot: %s", provider_map[source].name, err
                )
                raise SnapmNoSpaceError(
                    f"Insufficient free space for snapshot set {name}"
                ) from err

        self._check_recursion(origins)

        _suspend_journal()

        snapshots = []
        for source in provider_map:
            if S_ISBLK(stat(source).st_mode):
                mount = mounts[source]
            else:
                mount = source
            try:
                snapshots.append(
                    provider_map[source].create_snapshot(
                        origins[source], name, timestamp, mount, size_policies[source]
                    )
                )
            except SnapmError as err:
                _log_error("Error creating snapshot set member %s: %s", name, err)
                _resume_journal()
                for snapshot in snapshots:
                    snapshot.delete()
                raise SnapmPluginError(
                    f"Could not create all snapshots for set {name}"
                ) from err

        _resume_journal()

        for provider in set(provider_map.values()):
            provider.end_transaction()

        snapset = SnapshotSet(name, timestamp, snapshots)

        if boot or revert:
            _log_info(
                "Autoactivation required for bootable snapshot set '%s'", snapset.name
            )
            self._set_autoactivate(snapset, auto=True)
            snapset.activate()

        if boot:
            try:
                create_snapset_boot_entry(snapset)
                self._boot_cache.refresh_cache()
            except (OSError, ValueError) as err:
                _log_error("Failed to create snapshot set boot entry: %s", err)
                snapset.delete()
                raise SnapmCalloutError from err

        if revert:
            try:
                create_snapset_revert_entry(snapset)
                self._boot_cache.refresh_cache()
            except (OSError, ValueError) as err:
                _log_error("Failed to create snapshot set revert boot entry: %s", err)
                snapset.delete()
                raise SnapmCalloutError from err

        self.by_name[snapset.name] = snapset
        self.by_uuid[snapset.uuid] = snapset
        self.snapshot_sets.append(snapset)
        return snapset

    @suspend_signals
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

        # Remove references to old set
        self.by_name.pop(snapset.name)
        self.by_uuid.pop(snapset.uuid)
        self.snapshot_sets.remove(snapset)

        try:
            snapset.rename(new_name)
        except SnapmError as err:
            self.by_name[snapset.name] = snapset
            self.by_uuid[snapset.uuid] = snapset
            self.snapshot_sets.append(snapset)
            raise err

        self.by_name[snapset.name] = snapset
        self.by_uuid[snapset.uuid] = snapset
        self.snapshot_sets.append(snapset)
        return snapset

    @suspend_signals
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
            delete_snapset_revert_entry(snapset)
            snapset.delete()
            self.snapshot_sets.remove(snapset)
            self.by_name.pop(snapset.name)
            self.by_uuid.pop(snapset.uuid)
            deleted += 1
        self._boot_cache.refresh_cache()
        return deleted

    # pylint: disable=too-many-branches
    @suspend_signals
    def resize_snapshot_set(
        self, source_specs, name=None, uuid=None, default_size_policy=None
    ):
        """
        Resize snapshot set named ``name`` or having UUID ``uuid``.

        Request to resize each snapshot included in ``source_specs``
        according to the given size policy, or apply ``default_size_policy``
        if set.

        :param name: The name of the snapshot set to resize.
        :param uuid: The UUID of the snapshot set to resize.
        :param source_specs: A list of mount points and optional size
                                  policies.
        :param default_size_policy: A default size policy to apply to the
                                    resize.
        """
        snapset = self._snapset_from_name_or_uuid(name=name, uuid=uuid)

        if source_specs:
            # Parse size policies and normalise mount paths
            (sources, size_policies) = _parse_source_specs(
                source_specs, default_size_policy
            )
        else:
            sources = [snapshot.source for snapshot in snapset.snapshots]
            size_policies = {source: default_size_policy for source in sources}

        snapset.resize(sources, size_policies)

    @suspend_signals
    def revert_snapshot_set(self, name=None, uuid=None):
        """
        Revert snapshot set named ``name`` or having UUID ``uuid``.

        Request to revert each snapshot origin within each snapshot set
        to the state at the time the snapshot was taken.

        :param name: The name of the snapshot set to revert.
        :param uuid: The UUID of the snapshot set to revert.
        """
        snapset = self._snapset_from_name_or_uuid(name=name, uuid=uuid)

        _check_revert_snapshot_set(snapset)

        # Snapshot boot entry becomes invalid as soon as revert is initiated.
        delete_snapset_boot_entry(snapset)

        snapset.revert()

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

    @suspend_signals
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
            snapset.activate()
            activated += 1
        return activated

    @suspend_signals
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
            snapset.deactivate()
            deactivated += 1
        return deactivated

    def _set_autoactivate(self, snapset, auto=False):
        """
        Set autoactivation for ``snapset``.

        :param snapset: The ``SnapshotSet`` object to operate on.
        :param auto:  ``True`` to enable autoactivation or ``False`` otherwise.
        """
        _check_snapset_status(snapset, "set autoactivate status for")
        state = bool_to_yes_no(auto)
        _log_info("Setting autoactivation=%s for snapshot set %s", state, snapset.name)
        snapset.autoactivate = auto

    @suspend_signals
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
            self._set_autoactivate(snapset, auto=auto)
            changed += 1
        return changed

    @suspend_signals
    def split_snapshot_set(self, name, new_name, source_specs):
        """
        Split the snapshot set named `name` into two snapshot sets, with
        `new_name` containing the listed `sources` and `name` containing all
        remaining snapshots.

        If `new_name` is `None` the listed snapshot are removed from `name`
        and permanently deleted. This operation cannot be undone.

        :param name: The name of the snapshot set to split
        :param new_name: The name of the newly split off snapshot set
        :param sources: The list of sources to include in the new snapshot set
        """
        if name not in self.by_name:
            raise SnapmNotFoundError(f"Cannot find snapshot set named {name}")

        if new_name:
            self._validate_snapset_name(new_name)

        op = f"{'split' if new_name else 'prune'}"

        _log_info("Attempting to %s snapshot set '%s'", op, name)

        snapset = self.by_name[name]
        _check_snapset_status(snapset, op)

        if not source_specs:
            raise SnapmArgumentError(
                f"SnapshotSet {op} requires at least one source argument"
            )

        # Parse size policies and normalise mount paths
        (sources, size_policies) = _parse_source_specs(source_specs, None)

        if any(size_policies[source] is not None for source in sources):
            err_sources = ", ".join(
                [source for source in source_specs if ":" in source]
            )
            raise SnapmArgumentError(
                f"SnapshotSet {op} does not support size policies: {err_sources}"
            )

        missing_sources = [
            source for source in sources if source not in snapset.sources
        ]

        if missing_sources:
            raise SnapmNotFoundError(
                f"SnapshotSet '{snapset.name}' does not contain {', '.join(missing_sources)}"
            )

        timestamp = snapset.timestamp

        split_snapshots = [
            snapshot for snapshot in snapset.snapshots if snapshot.source in sources
        ]

        keep_snapshots = [
            snapshot for snapshot in snapset.snapshots if snapshot.source not in sources
        ]

        if not keep_snapshots:
            op_str = "Splitting" if new_name else "Pruning"
            raise SnapmArgumentError(
                f"{op_str} {', '.join((snap.source for snap in split_snapshots))} "
                f"from {snapset.name} would leave {snapset.name} empty"
            )

        _log_debug(
            "Splitting snapshot%s %s from snapshot set '%s'",
            "s" if len(split_snapshots) > 1 else "",
            ", ".join((snap.source for snap in split_snapshots)),
            snapset.name,
        )

        self.by_name.pop(snapset.name)
        self.by_uuid.pop(snapset.uuid)
        self.snapshot_sets.remove(snapset)

        # Build new SnapshotSet with old name to start with, then use
        # SnapshotSet.rename() to propagate new_name to members.
        new_set = SnapshotSet(snapset.name, timestamp, split_snapshots)

        # Update original snapshot set and re-add to lookup tables
        orig_set = SnapshotSet(snapset.name, timestamp, keep_snapshots)
        self.by_name[orig_set.name] = orig_set
        self.by_uuid[orig_set.uuid] = orig_set
        self.snapshot_sets.append(orig_set)

        if new_name:
            # Attempt to rename members to new_name
            try:
                new_set.rename(new_name)
            except SnapmError as err:
                self.by_name[snapset.name] = snapset
                self.by_uuid[snapset.uuid] = snapset
                self.snapshot_sets.append(snapset)
                raise err
        else:
            try:
                # Delete the pruned members
                new_set.delete()
            except SnapmError as err:
                _log_error(
                    "Failed to clean up pruned snapshot set members: %s (%s)",
                    err,
                    ", ".join(snapshot.name for snapshot in new_set.snapshots),
                )
            return orig_set

        # Add new snapshot set to lookup tables
        self.by_name[new_set.name] = new_set
        self.by_uuid[new_set.uuid] = new_set
        self.snapshot_sets.append(new_set)

        return new_set

    @suspend_signals
    def create_snapshot_set_boot_entry(self, name=None, uuid=None):
        """
        Create a snapshot boot entry for the specified snapshot set.

        :param name: The name of the snapshot set.
        :param uuid: The UUID of the snapshot set.
        """
        snapset = self._snapset_from_name_or_uuid(name=name, uuid=uuid)

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

    @suspend_signals
    def create_snapshot_set_revert_entry(self, name=None, uuid=None):
        """
        Create a revert boot entry for the specified snapshot set.

        :param name: The name of the snapshot set.
        :param uuid: The UUID of the snapshot set.
        """
        snapset = self._snapset_from_name_or_uuid(name=name, uuid=uuid)

        if snapset.revert_entry is not None:
            raise SnapmExistsError(
                f"Revert entry already associated with snapshot set {snapset.name}"
            )
        create_snapset_revert_entry(snapset)
        self._boot_cache.refresh_cache()


__all__ = [
    "Manager",
]
