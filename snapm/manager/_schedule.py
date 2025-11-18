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
from typing import Dict, List, Tuple, Union
from datetime import datetime, timedelta
from dataclasses import dataclass
from json import dumps, loads
from os.path import join
from enum import Enum
from math import ceil
import tempfile
import textwrap
import logging
import os

from snapm import (
    SnapmSystemError,
    SnapmArgumentError,
    SnapshotSet,
    SNAPM_SUBSYSTEM_SCHEDULE,
)
from ._boot import (
    delete_snapset_boot_entry,
    delete_snapset_revert_entry,
)
from ._timers import TimerStatus, TimerType, Timer

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


def _log_debug_schedule(msg, *args, **kwargs):
    """A wrapper for schedule subsystem debug logs."""
    _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_SCHEDULE}, **kwargs)


#: Garbage collect timer default calendarspec
_GC_CALENDAR_SPEC = "*-*-* *:10:00"

#: File mode for schedule configuration files
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
        return ", ".join(
            f"{key}={val}" for key, val in self.__dict__.items() if val != 0
        )

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

    @property
    def has_params(self):
        """
        Return ``True`` if this ``GcPolicyParams`` object has defined
        parameters or ``False`` otherwise.

        :returns: ``True`` if parameters are set else ``False``.
        :rtype: ``bool``
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

    @property
    def has_params(self):
        """
        Return ``True`` if this ``GcPolicyParams`` object has defined
        parameters or ``False`` otherwise.

        :returns: ``True`` if parameters are set else ``False``.
        :rtype: ``bool``
        """
        return True


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
        end = max(len(sets) - self.keep_count, 0)
        to_delete = sets[0:end]
        _log_debug_schedule(
            "%s garbage collecting: %s",
            repr(self),
            ", ".join(td.name for td in to_delete),
        )
        return to_delete

    @property
    def has_params(self):
        """
        Return ``True`` if this ``GcPolicyParams`` object has defined
        parameters or ``False`` otherwise.

        :returns: ``True`` if parameters are set else ``False``.
        :rtype: ``bool``
        """
        return self.keep_count > 0


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
            self.keep_years * 365.25
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
        limit = datetime.now() - self.to_timedelta()
        to_delete = [sset for sset in sets if sset.datetime < limit]
        _log_debug_schedule(
            "%s garbage collecting: %s",
            repr(self),
            ", ".join(td.name for td in to_delete),
        )
        return to_delete

    @property
    def has_params(self):
        """
        Return ``True`` if this ``GcPolicyParams`` object has defined
        parameters or ``False`` otherwise.

        :returns: ``True`` if parameters are set else ``False``.
        :rtype: ``bool``
        """
        return self.to_days() > 0


@dataclass
class GcPolicyParamsTimeline(GcPolicyParams):
    """
    Policy parameters for the TIMELINE policy type.

    ``keep_hourly`` (int): The maximum number of hourly snapshot sets to keep.
    ``keep_daily`` (int): The maximum number of daily snapshot sets to keep.
    ``keep_weekly`` (int): The maximum number of weekly snapshot sets to keep.
    ``keep_monthly`` (int): The maximum number of monthly snapshot sets to keep.
    ``keep_quarterly`` (int): The maximum number of quarterly snapshot sets keep.
    ``keep_yearly`` (int): The maximum number of yearly snapshot sets to keep.
    """

    _type = GcPolicyType.TIMELINE

    #: The maximum number of yearly snapshot sets to keep.
    keep_yearly: int = 0
    #: The maximum number of quarterly snapshot sets to keep.
    keep_quarterly: int = 0
    #: The maximum number of monthly snapshot sets to keep.
    keep_monthly: int = 0
    #: The maximum number of weekly snapshot sets to keep.
    keep_weekly: int = 0
    #: The maximum number of daily snapshot sets to keep.
    keep_daily: int = 0
    #: The maximum number of hourly snapshot sets to keep.
    keep_hourly: int = 0

    # pylint: disable=too-many-locals
    def evaluate(self, sets: List[SnapshotSet]) -> List[SnapshotSet]:
        """
        Evaluate the list of ``SnapshotSet`` objects in ``sets``
        against this ``GcPolicyParamsTimeline`` instance and return a list of
        ``SnapshotSet`` objects that should be garbage collected according
        to the configured ``keep_yearly``, ``keep_quarterly``, ``keep_monthly``,
        ``keep_weekly``, ``keep_daily``, and ``keep_hourly``.

        Snapshots can be classified into multiple categories (e.g., a snapshot
        on Monday Jan 1 is yearly, quarterly, monthly, weekly, daily, and hourly).
        A snapshot is only deleted if ALL of its applicable categories vote for
        deletion. If ANY category wants to keep it, the snapshot is retained.

        :param sets: The list of ``SnapshotSet`` objects to evaluate,
                              sorted in order of increasing creation date.
        :type sets: ``list[SnapshotSet]``.
        :returns: A list of ``SnapshotSet`` objects to garbage collect.
        :rtype: ``list[SnapshotSet]``
        """
        categories = ["yearly", "quarterly", "monthly", "weekly", "daily", "hourly"]

        def classify_snapshot_sets(
            sets: List[SnapshotSet],
        ) -> Tuple[Dict[str, List[SnapshotSet]], Dict[int, List[str]]]:
            """
            Classify snapshot sets into categories based on creation time.
            Snapshots can belong to MULTIPLE categories:

                * yearly (first snapshot set after midnight 1st Jan)
                * quarterly (first snapshot set after midnight 1st Apr, Jul, Oct)
                * monthly (first snapshot set after midnight 1st of month)
                * weekly (first snapshot set after midnight Monday)
                * daily (first snapshot set after midnight)
                * hourly (first snapshot set after top of hour)

            :param sets: The list of ``SnapshotSet`` objects to classify.
            :type sets: ``List[SnapshotSet]``
            :returns: A tuple of (classified dict, snapshot_categories dict)
            :rtype: ``Tuple[Dict[str, List[SnapshotSet]], Dict[int, List[str]]]``
            """
            classified = {category: [] for category in categories}
            seen_boundaries = {category: set() for category in categories}

            _log_debug_schedule(
                "%s: classifying %d snapshot sets", repr(self), len(sets)
            )

            # Track which categories each snapshot belongs to
            snapshot_categories = {}

            # pylint: disable=too-many-return-statements
            def get_boundary(dt, category):
                if category == "yearly":
                    return datetime(dt.year, 1, 1)
                if category == "quarterly":
                    q_month = ((dt.month - 1) // 3) * 3 + 1
                    return datetime(dt.year, q_month, 1)
                if category == "monthly":
                    return datetime(dt.year, dt.month, 1)
                if category == "weekly":
                    monday = dt - timedelta(days=dt.weekday())
                    return datetime(monday.year, monday.month, monday.day)
                if category == "daily":
                    return datetime(dt.year, dt.month, dt.day)
                if category == "hourly":
                    return datetime(dt.year, dt.month, dt.day, dt.hour)
                return None

            for sset in sets:
                dt = sset.datetime
                sset_categories = []

                for category in categories:
                    if category == "quarterly" and dt.month not in (1, 4, 7, 10):
                        continue
                    if category == "weekly" and dt.weekday() != 0:
                        continue

                    boundary = get_boundary(dt, category)
                    if dt >= boundary and boundary not in seen_boundaries[category]:
                        classified[category].append(sset)
                        seen_boundaries[category].add(boundary)
                        sset_categories.append(category)
                        # Continue checking other categories

                snapshot_categories[id(sset)] = sset_categories

            return classified, snapshot_categories

        # Build lists of categorised snapshot sets
        classified_sets, snapshot_categories = classify_snapshot_sets(sets)

        # Short cuts for each category
        yearly = classified_sets["yearly"]
        quarterly = classified_sets["quarterly"]
        monthly = classified_sets["monthly"]
        weekly = classified_sets["weekly"]
        daily = classified_sets["daily"]
        hourly = classified_sets["hourly"]

        for category in categories:
            members = classified_sets[category]
            _log_debug_schedule(
                "Classified %s: %s",
                category,
                ", ".join(c.name for c in members),
            )

        # Build a set of snapshots that should be KEPT by each category
        kept_by_category = {
            "yearly": set(
                yearly[max(len(yearly) - self.keep_yearly, 0) :]
                if self.keep_yearly
                else []
            ),
            "quarterly": set(
                quarterly[max(len(quarterly) - self.keep_quarterly, 0) :]
                if self.keep_quarterly
                else []
            ),
            "monthly": set(
                monthly[max(len(monthly) - self.keep_monthly, 0) :]
                if self.keep_monthly
                else []
            ),
            "weekly": set(
                weekly[max(len(weekly) - self.keep_weekly, 0) :]
                if self.keep_weekly
                else []
            ),
            "daily": set(
                daily[max(len(daily) - self.keep_daily, 0) :] if self.keep_daily else []
            ),
            "hourly": set(
                hourly[max(len(hourly) - self.keep_hourly, 0) :]
                if self.keep_hourly
                else []
            ),
        }

        for category in categories:
            _log_debug_schedule(
                "Selected %d sets for %s retention: %s",
                len(kept_by_category[category]),
                category,
                ", ".join(k.name for k in kept_by_category[category]),
            )

        # Determine which snapshots to delete
        # A snapshot is KEPT if ANY of its categories wants to keep it
        # A snapshot is DELETED only if ALL of its categories want to delete it
        to_delete = []

        for sset in sets:
            sset_cats = snapshot_categories.get(id(sset), [])

            # If snapshot has no categories, delete it
            if not sset_cats:
                to_delete.append(sset)
                continue

            # Check if ANY category wants to keep this snapshot
            should_keep = any(
                sset in kept_by_category[category] for category in sset_cats
            )

            if not should_keep:
                to_delete.append(sset)

        _log_debug_schedule(
            "%s garbage collecting: %s",
            repr(self),
            ", ".join(td.name for td in to_delete),
        )
        return to_delete

    @property
    def has_params(self):
        """
        Return ``True`` if this ``GcPolicyParams`` object has defined
        parameters or ``False`` otherwise.

        :returns: ``True`` if parameters are set else ``False``.
        :rtype: ``bool``
        """
        return any(
            (
                self.keep_yearly,
                self.keep_quarterly,
                self.keep_monthly,
                self.keep_weekly,
                self.keep_daily,
                self.keep_hourly,
            )
        )


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
        if policy_type not in _TYPE_MAP:
            raise SnapmArgumentError(f"Invalid GcPolicyType: {policy_type}")

        if not policy_name:
            raise SnapmArgumentError("GcPolicy name cannot be empty")

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
            f"Name: {self.name}\n"
            f"Type: {self.type.value}\n"
            f"Params: {str(self.params)}"
        )

    def __repr__(self):
        """
        Return a machine readable string representation of this ``GcPolicy``.

        :returns: A machine readable string.
        :rtype: ``str``
        """
        return (
            f"GcPolicy('{self.name}', "
            f"GcPolicyType.{self.type.name}, "
            f"{self.params.to_dict()})"
        )

    def __eq__(self, other):
        """
        Test for ``GcPolicy`` equality.
        """
        return (
            self.name == other.name
            and self.type == other.type
            and self.params == other.params
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
    def name(self):
        """
        This ``GcPolicy`` instance's name.
        """
        return self._name

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
        Instantiate a ``GcPolicy`` object from a JSON string in
        ``value``.

        :param value: A JSON string describing a ``GcPolicyParams`` instance.
        :type data: ``str``
        :returns: An instance of a ``GcPolicyParams`` subclass reflecting
                  the values in the JSON string ``value``.
        :rtype: ``GcPolicyParams``
        """
        return cls.from_dict(loads(value))

    @classmethod
    def from_cmd_args(cls, cmd_args):
        """
        Initialise garbage collection policy from command line arguments.

        Construct a new ``GcPolicy`` object from the command line
        arguments in ``cmd_args``. The ``GcPolicyType`` is determined by
        the ``policy_type`` argument, and the type-specific policy
        parameters are initialised from the remaining arguments.

        :param cmd_args: The command line selection arguments.
        :returns: A new ``GcPolicy`` instance
        :rtype: ``GcPolicy``
        """
        policy_type = cmd_args.policy_type.upper()
        name = cmd_args.schedule_name
        if policy_type == GcPolicyType.ALL.name:
            params = {}
        elif policy_type == GcPolicyType.COUNT.name:
            params = {"keep_count": cmd_args.keep_count}
        elif policy_type == GcPolicyType.AGE.name:
            params = {
                "keep_years": cmd_args.keep_years,
                "keep_months": cmd_args.keep_months,
                "keep_weeks": cmd_args.keep_weeks,
                "keep_days": cmd_args.keep_days,
            }
        elif policy_type == GcPolicyType.TIMELINE.name:
            params = {
                "keep_yearly": cmd_args.keep_yearly,
                "keep_quarterly": cmd_args.keep_quarterly,
                "keep_monthly": cmd_args.keep_monthly,
                "keep_weekly": cmd_args.keep_weekly,
                "keep_daily": cmd_args.keep_daily,
                "keep_hourly": cmd_args.keep_hourly,
            }
        else:
            raise SnapmArgumentError(f"Unknown policy type: {policy_type}")
        return GcPolicy(name, GcPolicyType[policy_type], params)

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

    @property
    def has_params(self):
        """
        Return ``True`` if this ``GcPolicy`` object has defined policy
        parameters or ``False`` otherwise.

        :returns: ``True`` if parameters are set else ``False``.
        :rtype: ``bool``
        """
        return self.params.has_params


# pylint: disable=too-many-public-methods
class Schedule:
    """
    An individual snapshot schedule instance with create and garbage
    collection timers. Tracks timer configuration, name, sources, size
    policies, enabled/disabled, nr snapshots, next elapse.
    """

    def __init__(
        self,
        name: str,
        sources: List[str],
        default_size_policy: Union[str, None],
        autoindex: bool,
        calendarspec: str,
        gc_policy: GcPolicy,
        boot=False,
        revert=False,
    ):
        """
        Initialise a new ``Schedule`` instance.

        :param name: The name of the ``Schedule``.
        :type name: ``str``
        :param sources: The source specs to include in this ``Schedule``.
        :type sources: ``list[str]``
        :param default_size_policy: The default size policy for this
                                    ``Schedule``.
        :type default_size_policy: ``str``
        :param autoindex: Enable autoindex names for this ``Schedule``.
        :type autoindex: ``bool``
        :param calendarspec: The ``OnCalendar`` expression for this ``Schedule``.
        :type calendarspec: ``str``
        :param policy: The garbage collection policy for this ``Schedule``.
        :type policy: ``GcPolicy``
        :returns: The new ``Schedule`` instance.
        :rtype: ``Schedule``
        """
        self._name = name
        self._sources = sources
        self._default_size_policy = default_size_policy or None
        self._autoindex = autoindex
        self._gc_policy = gc_policy
        self._timer = Timer(TimerType.CREATE, name, calendarspec)
        self._boot = boot
        self._revert = revert
        self._sched_path = None

    def __str__(self):
        """
        Return a human-readable representation of this ``Schedule``.

        :returns: A human-readable string.
        :rtype: ``str``
        """
        as_dict = self.to_dict()
        as_dict["enabled"] = "yes" if self.enabled else "no"
        as_dict["running"] = "yes" if self.running else "no"
        as_dict["boot"] = "yes" if self.boot else "no"
        as_dict["revert"] = "yes" if self.revert else "no"
        as_dict["gc_policy"] = textwrap.indent("\n" + str(self.gc_policy), 4 * " ")
        as_dict["sources"] = ", ".join(as_dict["sources"])
        as_dict["next_elapse"] = self.next_elapse
        as_dict.pop("autoindex")
        return "\n".join(
            f"{key.title().replace('_', '')}: {value}" for key, value in as_dict.items()
        )

    def __repr__(self):
        """
        Return a machine-readable representation of this ``Schedule``.

        :returns: A machine-readable string.
        :rtype: ``str``
        """
        return (
            f"Schedule('{self.name}', {self.sources}, "
            f"'{self.default_size_policy}', {self.autoindex}, "
            f"'{self.calendarspec}', {repr(self.gc_policy)})"
        )

    def __eq__(self, other):
        """
        Test for ``Schedule`` equality.
        """
        return (
            self.name == other.name
            and self.sources == other.sources
            and self.default_size_policy == other.default_size_policy
            and self.autoindex == other.autoindex
            and self.calendarspec == other.calendarspec
            and self.gc_policy == other.gc_policy
            and self.boot == other.boot
            and self.revert == other.revert
        )

    def to_dict(self):
        """
        Return a dictionary representation of this ``Schedule``.

        :returns: This ``Schedule`` as a dictionary.
        :rtype: ``dict``
        """
        attrs = [
            "name",
            "sources",
            "default_size_policy",
            "autoindex",
            "calendarspec",
            "boot",
            "revert",
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
    def sources(self):
        """
        The source specs value for this ``Schedule``.
        """
        return self._sources

    @property
    def default_size_policy(self):
        """
        The default size policy for this ``Schedule``.
        """
        return self._default_size_policy

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
    def next_elapse(self):
        """
        Return the ``next_elapse`` time of this ``Schedule``'s timer.
        """
        return self._timer.next_elapse

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

    @property
    def boot(self):
        """
        Return ``True`` if this ``Schedule`` is configured to enable snapshot
        set boot entries when creating snapshot sets.
        """
        return self._boot

    @property
    def revert(self):
        """
        Return ``True`` if this ``Schedule`` is configured to enable snapshot
        set revert entries when creating snapshot sets.
        """
        return self._revert

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
        self._timer.stop()
        self._timer.disable()
        self.gc_policy.stop()
        self.gc_policy.disable()

    def gc(self, sets: List[SnapshotSet]) -> List[str]:
        """
        Apply the configured garbage collection policy for this ``Schedule``.
        """
        to_delete = self.gc_policy.evaluate(sets)
        deleted = []
        for snapshot_set in to_delete:
            delete_snapset_boot_entry(snapshot_set)
            delete_snapset_revert_entry(snapshot_set)
            deleted.append(snapshot_set.name)
            snapshot_set.delete()
        return deleted

    def write_config(self, sched_dir: str):
        """
        Write this ``Schedule``'s configuration to disk.

        :param sched_dir: The path at which to write the configuration file.
        :type sched_dir: ``str``
        """
        json = self.json(pretty=True)
        sched_path = join(sched_dir, f"{self.name}.json")
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
                f"Filesystem error writing schedule temporary file for '{sched_path}': {err}"
            ) from err
        self._sched_path = sched_path

    def delete_config(self):
        """
        Delete this ``Schedule``'s on-disk configuration.
        """
        if not self._sched_path:
            return
        os.unlink(self._sched_path)

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
        policy = GcPolicy.from_dict(data.pop("gc_policy"))
        return Schedule(
            data["name"],
            data["sources"],
            data["default_size_policy"],
            data["autoindex"],
            data["calendarspec"],
            policy,
            boot=data["boot"],
            revert=data["revert"],
        )

    @classmethod
    def from_file(cls, sched_file):
        """
        Initialise a new ``Schedule`` instance from an on-disk JSON
        configuration file.

        :param sched_file: The path to the schedule configuration file.
        :type sched_file: ``str``
        :returns: A new ``Schedule`` instance.
        :rtype: ``Schedule``
        """
        try:
            with open(sched_file, "r", encoding="utf8") as fp:
                json = fp.read()
                sched_dict = loads(json)
        except OSError as err:  # pragma: no cover
            raise SnapmSystemError(
                f"Filesystem error reading schedule file '{sched_file}': {err}"
            ) from err

        schedule = Schedule.from_dict(sched_dict)
        schedule._sched_path = sched_file

        return schedule


__all__ = [
    "Schedule",
    "GcPolicy",
    "GcPolicyType",
    "GcPolicyParams",
]
