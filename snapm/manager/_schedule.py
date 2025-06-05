# Copyright Red Hat
#
# snapm/manager/_schedule.py - Snapshot Manager boot support
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Snapshot set scheduling abstractions for Snapshot Manager.
"""
from dataclasses import dataclass
from datetime import timedelta
from json import dumps, loads
from os.path import join
from typing import List
from enum import Enum
from math import ceil
import tempfile
import logging
import os

from snapm import (
    SnapmSystemError,
    SnapshotSet,
)
from ._timers import TimerStatus, TimerType, Timer

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error

#: Garbage collect timer default calendarspec
_GC_CALENDAR_SPEC = "*-*-* 01:00:00"

#: File mode for schdule configuration files
_SCHEDULE_CONF_FILE_MODE = 0o644


class GcPolicyType(Enum):
    """
    Garbage collection policy types enum.
    """

    ALL = "All"
    COUNT = "Count"
    AGE = "Age"
    TIMELINE = "Timeline"


class GcPolicyParams:
    """
    Abstract base class for garbage collection policy parameters.
    """

    def __str__(self):
        """
        Return a human readable string representation of this
        ``GcPolicyParams`` instance.

        :returns: A human readable string.
        :rtype: ``str``
        """
        return ", ".join([f"{key}={value}" for key, value in self.__dict__.items()])

    def to_dict(self):
        """
        Return a dictionary representation of this ``GcPolicyParams``.

        :returns: This ``GcPolicyParams`` as a dictionary.
        :rtype: ``dict``
        """
        return self.__dict__.copy()

    def json(self, pretty=False):
        """
        Return a JSON representation of this ``GcPolicyParams``.

        :returns: This ``GcPolicyParams`` as a JSON string.
        :rtype: ``str``
        """
        return dumps(self.to_dict(), indent=4 if pretty else None)

    def evaluate(self, sets: List[SnapshotSet]) -> List[SnapshotSet]:
        """
        Evaluate the list of ``SnapshotSet`` objects in ``sets``
        against this set of ``GcPolicyParams`` and return a list of
        ``SnapshotSet`` objects that should be garbage collected.

        :param sets: The list of ``SnapshotSet`` objects to evaluate,
                              sorted in order of increasing creation date.
        :type sets: ``list[SnapshotSet]``.
        :returns: A list of ``SnapshotSet`` objects to garbage collect.
        :rtype: ``list[SnapshotSet]``
        """
        raise NotImplementedError


@dataclass
class GcPolicyParamsAll(GcPolicyParams):
    """
    Policy parameters for the ALL policy type.
    """

    _type = GcPolicyType.ALL

    def evaluate(self, sets: List[SnapshotSet]) -> List[SnapshotSet]:
        """
        Evaluate the list of ``SnapshotSet`` objects in ``sets``
        against this ``GcPolicyParamsAll`` instance and return a list of
        ``SnapshotSet`` objects that should be garbage collected.

        Since this ``GcPolicyType`` always retains all ``SnapshotSet`` objects
        passed to it it will always return the empty list.

        :param sets: The list of ``SnapshotSet`` objects to evaluate,
                              sorted in order of increasing creation date.
        :type sets: ``list[SnapshotSet]``.
        :returns: A list of ``SnapshotSet`` objects to garbage collect.
        :rtype: ``list[SnapshotSet]``
        """
        return []


@dataclass
class GcPolicyParamsCount(GcPolicyParams):
    """
    Policy parameters for the COUNT policy type.

    ``keep_count`` (int): The count of snapshot sets to keep.
    """

    _type = GcPolicyType.COUNT

    #: Retain ``keep_count`` number of snapshot sets.
    keep_count: int = 0

    def evaluate(self, sets: List[SnapshotSet]) -> List[SnapshotSet]:
        """
        Evaluate the list of ``SnapshotSet`` objects in ``sets``
        against this ``GcPolicyParamsCount`` instance and return a list of
        ``SnapshotSet`` objects that should be garbage collected according
        to the configured ``keep_count``.

        :param sets: The list of ``SnapshotSet`` objects to evaluate,
                              sorted in order of increasing creation date.
        :type sets: ``list[SnapshotSet]``.
        :returns: A list of ``SnapshotSet`` objects to garbage collect.
        :rtype: ``list[SnapshotSet]``
        """
        return sets[0 : len(sets) - self.keep_count]


@dataclass
class GcPolicyParamsAge(GcPolicyParams):
    """
    Policy parameters for the AGE policy type.

    ``keep_years:`` (int): The age in years to retain snapshot sets.
    ``keep_months:`` (int): The age in months to retain snapshot sets.
    ``keep_weeks:`` (int): The age in weeks to retain snapshot sets.
    ``keep_days:`` (int): The age in days to retain snapshot sets.
    """

    _type = GcPolicyType.AGE

    #: The maximum age in years to retain snapshot sets.
    keep_years: int = 0
    #: The maximum age in months to retain snapshot sets.
    keep_months: int = 0
    #: The maximum age in weeks to retain snapshot sets.
    keep_weeks: int = 0
    #: The maximum age in days to retain snapshot sets.
    keep_days: int = 0

    def to_days(self):
        """
        Return the total number of days represented by this
        ``GcPolicyParamsAge`` as an integer.

        :returns: The number of days to retain snapshot sets according to
                  this ``GcPolicyParamsAge`` object.
        :rtype: ``int``
        """
        return ceil(
            self.keep_years * 362.25
            + self.keep_months * 30.44
            + self.keep_weeks * 7
            + self.keep_days
        )

    def to_timedelta(self):
        """
        Return this ``GcPolicyParamsAge`` object as a ``datetime.timedelta``
        object.

        :returns: The time to retain snapshot sets as a ``timedelta`` object.
        :rtype: ``datetime.timedelta``
        """
        return timedelta(days=self.to_days())

    def evaluate(self, sets: List[SnapshotSet]) -> List[SnapshotSet]:
        """
        Evaluate the list of ``SnapshotSet`` objects in ``sets``
        against this ``GcPolicyParamsAge`` instance and return a list of
        ``SnapshotSet`` objects that should be garbage collected according
        to the configured ``keep_years``, ``keep_months``, ``keep_weeks``,
        and ``keep_days``

        :param sets: The list of ``SnapshotSet`` objects to evaluate,
                              sorted in order of increasing creation date.
        :type sets: ``list[SnapshotSet]``.
        :returns: A list of ``SnapshotSet`` objects to garbage collect.
        :rtype: ``list[SnapshotSet]``
        """
        # max_age = self.to_days()
        # return [sset for sset in sets if age_days(sset) < max_age
        raise NotImplementedError


@dataclass
class GcPolicyParamsTimeline(GcPolicyParams):
    """
    Policy parameters for the TIMELINE policy type.

    ``keep_hourly`` (int): The maximum number of hourly snapshot sets to keep.
    ``keep_daily`` (int): The maximum nuber of daily snapshot sets to keep.
    ``keep_weekly`` (int): The maximum number of weekly snapshot sets to keep.
    ``keep_monthly`` (int): The maximum nuber of monthly snapshot sets to keep.
    ``keep_quarterly`` (int): The maximum number of quarterly snapshot sets keep.
    ``keep_yearly`` (int): The maximum number of yearly snapshot sets to keep.
    """

    _type = GcPolicyType.TIMELINE

    #: The maximum number of hourly snapshot sets to keep.
    keep_hourly: int = 0
    #: The maximum number of daily snapshot sets to keep.
    keep_daily: int = 0
    #: The maximum number of weekly snapshot sets to keep.
    keep_weekly: int = 0
    #: The maximum number of monthly snapshot sets to keep.
    keep_monthly: int = 0
    #: The maximum number of quaterly snapshot sets to keep.
    keep_quarterly: int = 0
    #: The maximum number of yearly snapshot sets to keep.
    keep_yearly: int = 0

    def evaluate(self, sets: List[SnapshotSet]) -> List[SnapshotSet]:
        """
        Evaluate the list of ``SnapshotSet`` objects in ``sets``
        against this ``GcPolicyParamsTimeline`` instance and return a list of
        ``SnapshotSet`` objects that should be garbage collected according
        to the configured ``keep_yearly``, ``keep_monthly``, ``keep_weekly``,
        and ``keep_daily``

        :param sets: The list of ``SnapshotSet`` objects to evaluate,
                              sorted in order of increasing creation date.
        :type sets: ``list[SnapshotSet]``.
        :returns: A list of ``SnapshotSet`` objects to garbage collect.
        :rtype: ``list[SnapshotSet]``
        """
        # hourly = hourly_sets(sets)
        # daily = daily_sets(sets)
        # weekly = weekly_sets(sets)
        # monthly = monthly_sets(sets)
        # quarterly = quarterly_sets(sets)
        # yearly = yearly_sets(sets)
        #
        raise NotImplementedError


#: Mapping from ``GcPolicyType`` values to ``GcPolicyParams`` subclasses.
_TYPE_MAP = {
    GcPolicyType.ALL: GcPolicyParamsAll,
    GcPolicyType.COUNT: GcPolicyParamsCount,
    GcPolicyType.AGE: GcPolicyParamsAge,
    GcPolicyType.TIMELINE: GcPolicyParamsTimeline,
}


class GcPolicy:
    """
    An instance of a garbage collection policy.
    """

    def __init__(self, policy_name: str, policy_type: GcPolicyType, params: dict):
        """
        Initialise a new GcPolicy.

        :param policy_type: The policy type.
        :type policy_type: ``GcPolicyType``
        :param params: A dictionary of parameters suitable for ``policy_type``.
        :type params: ``dict``
        """
        self._name = policy_name
        self._type = policy_type
        self._params = _TYPE_MAP[policy_type](**params)
        self._timer = Timer(TimerType.GC, policy_name, _GC_CALENDAR_SPEC)

    def __str__(self):
        """
        Return a human readable string representation of this ``GcPolicy``.

        :returns: A human readable string.
        :rtype: ``str``
        """
        return (
            f"name: {self._name}\n"
            f"type: {self._type.value}\n"
            f"params: {self._params}"
        )

    def __repr__(self):
        """
        Return a machine readable string representation of this ``GcPolicy``.

        :returns: A machine readable string.
        :rtype: ``str``
        """
        return (
            f'GcPolicy("{self._name}", '
            f"GcPolicyType.{self._type.name}, "
            f"{self._params.to_dict()})"
        )

    def to_dict(self):
        """
        Return a dictionary representation of this ``GcPolicy``.

        :returns: This ``GcPolicy`` as a dictionary.
        :rtype: ``dict``
        """
        params_dict = {"policy_name": self._name, "policy_type": self.type.name}
        params_dict.update(self.params.to_dict())
        return params_dict

    def json(self, pretty=False):
        """
        Return a JSON representation of this ``GcPolicy``.

        :returns: This ``GcPolicy`` as a JSON string.
        :rtype: ``str``
        """
        return dumps(self.to_dict(), indent=4 if pretty else None)

    @property
    def params(self):
        """
        This ``GcPolicy`` instance's ``GcPolicyParams`` value.
        """
        return self._params

    @property
    def type(self):
        """
        This ``GcPolicy`` instance's ``GcPolicyType`` value.
        """
        return self._type

    @property
    def enabled(self):
        """
        Return ``True`` if this ``GcPolicy`` and its corresponding timer are
        enabled, and ``False`` otherwise.
        """
        return self._timer.status in (TimerStatus.ENABLED, TimerStatus.RUNNING)

    @property
    def running(self):
        """
        Return ``True`` if this ``GcPolicy`` and its corresponding timer are
        enabled, and ``False`` otherwise.
        """
        return self._timer.status == TimerStatus.RUNNING

    def enable(self):
        """
        Enable this ``GcPolicy`` and its corresponding timer.
        """
        self._timer.enable()

    def start(self):
        """
        Start this the timer for this ``GcPolicy``.
        """
        self._timer.start()

    def stop(self):
        """
        Stop the timer for this ``GcPolicy``.
        """
        self._timer.stop()

    def disable(self):
        """
        Disable this ``GcPolicy`` and its corresponding timer.
        """
        self._timer.disable()

    @classmethod
    def from_dict(cls, data):
        """
        Instantiate a ``GcPolicy`` object from values in ``data``.

        :param data: A dictionary describing a ``GcPolicy`` instance.
        :type data: ``dict``
        :returns: An instance of ``GcPolicy`` reflecting the values in
                  ``data``.
        :rtype: ``GcPolicy``
        """
        policy_type = GcPolicyType[data["policy_type"]]
        data.pop("policy_type")

        policy_name = data["policy_name"]
        data.pop("policy_name")

        return cls(policy_name, policy_type, data)

    @classmethod
    def from_json(cls, value):
        """
        Instantiate a ``GcPolicyParams`` object from a JSON string in
        ``value``.

        :param value: A JSON string describing a ``GcPolicyParams`` instance.
        :type data: ``str``
        :returns: An instance of a ``GcPolicyParams`` subclass reflecting
                  the values in the JSON string ``value``.
        :rtype: ``GcPolicyParams``
        """
        return cls.from_dict(loads(value))

    def evaluate(self, sets: List[SnapshotSet]):
        """
        Evaluate the list of ``SnapshotSet`` objects in ``sets``
        against this ``GcPolicy`` and return a list of ``SnapshotSet`` objects
        that should be garbage collected.

        :param sets: The list of ``SnapshotSet`` objects to evaluate,
                              sorted in order of increasing creation date.
        :type sets: ``list[SnapshotSet]``.
        :returns: A list of ``SnapshotSet`` objects to garbage collect.
        :rtype: ``list[SnapshotSet]``
        """
        return self.params.evaluate(sets)


class Schedule:
    """
    An individual snapshot schedule instance with create and garbage
    collection timers. Tracks timer configuration, name, sources, size
    policies, enabled/disabled, nr snapshots, next elapse.
    """

    def __init__(
        self,
        name: str,
        source_specs: List[str],
        default_size_policy: str,
        autoindex: bool,
        calendarspec: str,
        gc_policy: GcPolicy,
    ):
        """
        Initialse a new ``Schedule`` instance.

        :param name: The name of the ``Schedule``.
        :type name: ``str``
        :param source_specs: The souce specs to include in this ``Schedule``.
        :type source_specs: ``list[str]``
        :param default_size_policy: The default size policy for this
                                    ``Schedule``.
        :type default_size_policy: ``str``
        :param autoindex: Enable autoindex names for this ``Schedule``.
        :type autoindex: ``bool``
        :param calendarspec: The ``OnCalendar`` expression for this ``Schedule``.
        :type calendarspec: ``str``
        :param gc_policy: The garbage collection policy for this ``Schedule``.
        :type gc_policy: ``GcPolicy``
        :returns: The new ``Schedule`` instance.
        :rtype: ``Schedule``
        """
        self._name = name
        self._source_specs = source_specs
        self._default_size_policy = default_size_policy
        self._autoindex = autoindex
        self._gc_policy = gc_policy
        self._timer = Timer(TimerType.CREATE, name, calendarspec)
        self._sched_path = None

    def __str__(self):
        return "\n".join([f"{key}={value}" for key, value in self.to_dict().items()])

    def to_dict(self):
        """
        Return a dictionary representation of this ``Schedule``.

        :returns: This ``Schedule`` as a dictionary.
        :rtype: ``dict``
        """
        attrs = [
            "name",
            "source_specs",
            "default_size_policy",
            "autoindex",
            "calendarspec",
        ]
        sched_dict = {attr: getattr(self, attr) for attr in attrs}
        sched_dict["gc_policy"] = self.gc_policy.to_dict()
        return sched_dict

    def json(self, pretty=False):
        """
        Return a JSON representation of this ``Schedule``.

        :returns: This ``Schedule`` as a JSON string.
        :rtype: ``str``
        """
        return dumps(self.to_dict(), indent=4 if pretty else None)

    @property
    def name(self):
        """
        The name of this ``Schedule``.
        """
        return self._name

    @property
    def source_specs(self):
        """
        The source specs value for this ``Schedule``.
        """
        return self._source_specs

    @property
    def default_size_policy(self):
        """
        The default size policy for this ``Schedule``.
        """
        return self._default_size_policy or ""

    @property
    def autoindex(self):
        """
        The autoindex property for this ``Schedule``.
        """
        return self._autoindex

    @property
    def gc_policy(self):
        """
        The garbage collection policy for this ``Schedule``.
        """
        return self._gc_policy

    @property
    def calendarspec(self):
        """
        The OnCalendar expression for the timer associated with this
        ``Schedule`` instance.
        """
        return self._timer.calendarspec.original

    @property
    def enabled(self):
        """
        Return ``True`` if this ``Schedule`` and its corresponding timer are
        enabled, and ``False`` otherwise.
        """
        return self._timer.status in (TimerStatus.ENABLED, TimerStatus.RUNNING)

    @property
    def running(self):
        """
        Return ``True`` if this ``Schedule`` and its corresponding timer are
        enabled, and ``False`` otherwise.
        """
        return self._timer.status == TimerStatus.RUNNING and self.gc_policy.running

    def enable(self):
        """
        Enable this ``Schedule`` and its corresponding timers.
        """
        self._timer.enable()
        self.gc_policy.enable()

    def start(self):
        """
        Start this the timer for this ``Schedule``.
        """
        self._timer.start()
        self.gc_policy.start()

    def stop(self):
        """
        Stop the timer for this ``Schedule``.
        """
        self._timer.stop()
        self.gc_policy.stop()

    def disable(self):
        """
        Disable this ``Schedule`` and its corresponding timer.
        """
        self._timer.disable()
        self.gc_policy.disable()

    def gc(self, sets: List[SnapshotSet]):
        """
        Apply the configured garbage collection policy for this ``Schedule``.
        """
        to_delete = self.gc_policy.evaluate(sets)
        for snapshot_set in to_delete:
            snapshot_set.delete()

    def delete(self):
        """
        Delete this ``Schedule``'s on-disk configration.
        """
        if not self._sched_path:
            return
        os.unlink(self._sched_path)

    def write_config(self, sched_dir: str):
        """
        Write this ``Schedule``'s configuration to disk.

        :param sched_dir: The path at which to write the configuration file.
        :type sched_dir: ``str``
        """
        json = self.json(pretty=True)
        sched_path = join(sched_dir, f"{self.name}.conf")
        try:
            # Write the schedule configuration file atomically
            fd, tmp_path = tempfile.mkstemp(dir=sched_dir, prefix=".tmp_", text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf8") as f:
                    f.write(json)
                    f.flush()
                    os.fdatasync(f.fileno())
                os.rename(tmp_path, sched_path)
                os.chmod(sched_path, _SCHEDULE_CONF_FILE_MODE)

                # Ensure directory metadata is written to disk
                dir_fd = os.open(sched_dir, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError as err:  # pragma: no cover
                os.unlink(tmp_path)
                raise SnapmSystemError(
                    f"Filesystem error writing schedule file '{sched_path}': {err}"
                ) from err
        except OSError as err:  # pragma: no cover
            raise SnapmSystemError(
                f"Filesystem error writing schedule temporary file '{tmp_path}': {err}"
            ) from err
        self._sched_path = sched_path

    @classmethod
    def from_dict(cls, data):
        """
        Instantiate a ``Schedule`` object from values in ``data``.

        :param data: A dictionary describing a ``Schedule`` instance.
        :type data: ``dict``
        :returns: An instance of ``Schedule`` reflecting the values in
                  ``data``.
        :rtype: ``GcPolicyParams``
        """
        gc_policy = GcPolicy.from_dict(data.pop("gc_policy"))
        return Schedule(
            data["name"],
            data["source_specs"],
            data["default_size_policy"],
            data["autoindex"],
            data["calendarspec"],
            gc_policy,
        )

    @classmethod
    def from_file(cls, sched_path):
        """
        Initialise a new ``Schedule`` instance from an on-disk JSON
        configuration file.

        :param sched_path: The path to the schedule configuration file.
        :type sched_path: ``str``
        :returns: A new ``Schedule`` instance.
        :rtype: ``Schedule``
        """
        try:
            with open(sched_path, "r", encoding="utf8") as fp:
                json = fp.read()
                sched_dict = loads(json)
        except OSError as err:
            raise SnapmSystemError(
                f"Filesystem error reading schedule file '{sched_path}': {err}"
            ) from err
        return Schedule.from_dict(sched_dict)


__all__ = [
    "Schedule",
    "GcPolicy",
    "GcPolicyType",
    "GcPolicyParams",
]
