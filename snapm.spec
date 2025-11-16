%global summary A set of tools for managing snapshots

Name:		snapm
Version:	0.5.2
Release:	1%{?dist}
Summary:	%{summary}

License:	Apache-2.0
URL:		https://github.com/snapshotmanager/%{name}
Source0:	%{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:	noarch

BuildRequires:	boom-boot
BuildRequires:	lvm2
BuildRequires:	make
BuildRequires:	stratis-cli
BuildRequires:	stratisd
BuildRequires:	systemd-rpm-macros
BuildRequires:	pyproject-rpm-macros
BuildRequires:	python3-boom
BuildRequires:	python3-dateutil
BuildRequires:	python3-dbus-client-gen
BuildRequires:	python3-dbus-python-client-gen
BuildRequires:	python3-devel
BuildRequires:	python3-justbytes
BuildRequires:	python3-packaging
BuildRequires:	python3-pip
BuildRequires:	python3-psutil
BuildRequires:	python3-pytest
BuildRequires:	python3-setuptools
BuildRequires:	python3-sphinx
BuildRequires:	python3-wcwidth
BuildRequires:	python3-wheel
%if 0%{?fedora}
BuildRequires: libfaketime
%endif

Requires: python3-snapm = %{version}-%{release}
Recommends: boom-boot

%package -n python3-snapm
Summary: %{summary}

%package -n python3-snapm-doc
Summary: %{summary}

%description
Snapshot manager (snapm) is a tool for managing sets of snapshots on Linux
systems.  The snapm tool allows snapshots of multiple volumes to be captured at
the same time, representing the system state at the time the set was created.

%description -n python3-snapm
Snapshot manager (snapm) is a tool for managing sets of snapshots on Linux
systems.  The snapm tool allows snapshots of multiple volumes to be captured at
the same time, representing the system state at the time the set was created.

This package provides the python3 snapm module.

%description -n python3-snapm-doc
Snapshot manager (snapm) is a tool for managing sets of snapshots on Linux
systems.  The snapm tool allows snapshots of multiple volumes to be captured at
the same time, representing the system state at the time the set was created.

This package provides the python3 snapm module documentation in HTML format.

%prep
%autosetup -p1 -n %{name}-%{version}

%build
%pyproject_wheel

%{make_build} -C doc html
rm doc/_build/html/.buildinfo
mv doc/_build/html doc/html
rm -rf doc/html/_sources doc/_build
rm -f doc/*.rst doc/Makefile doc/conf.py

%install
%pyproject_install

mkdir -p ${RPM_BUILD_ROOT}/%{_sysconfdir}/%{name}/plugins.d
mkdir -p ${RPM_BUILD_ROOT}/%{_sysconfdir}/%{name}/schedule.d
%{__install} -p -m 644 etc/%{name}/snapm.conf ${RPM_BUILD_ROOT}/%{_sysconfdir}/%{name}
%{__install} -p -m 644 etc/%{name}/plugins.d/lvm2-cow.conf ${RPM_BUILD_ROOT}/%{_sysconfdir}/%{name}/plugins.d
%{__install} -p -m 644 etc/%{name}/plugins.d/lvm2-thin.conf ${RPM_BUILD_ROOT}/%{_sysconfdir}/%{name}/plugins.d
%{__install} -p -m 644 etc/%{name}/plugins.d/stratis.conf ${RPM_BUILD_ROOT}/%{_sysconfdir}/%{name}/plugins.d

mkdir -p ${RPM_BUILD_ROOT}/%{_mandir}/man8
mkdir -p ${RPM_BUILD_ROOT}/%{_mandir}/man5
%{__install} -p -m 644 man/man8/snapm.8 ${RPM_BUILD_ROOT}/%{_mandir}/man8
%{__install} -p -m 644 man/man5/snapm.conf.5 ${RPM_BUILD_ROOT}/%{_mandir}/man5
%{__install} -p -m 644 man/man5/snapm-plugins.d.5 ${RPM_BUILD_ROOT}/%{_mandir}/man5
%{__install} -p -m 644 man/man5/snapm-schedule.d.5 ${RPM_BUILD_ROOT}/%{_mandir}/man5

mkdir -p ${RPM_BUILD_ROOT}/%{_unitdir}
%{__install} -p -m 644 systemd/snapm-create@.service ${RPM_BUILD_ROOT}/%{_unitdir}
%{__install} -p -m 644 systemd/snapm-create@.timer ${RPM_BUILD_ROOT}/%{_unitdir}
%{__install} -p -m 644 systemd/snapm-gc@.service ${RPM_BUILD_ROOT}/%{_unitdir}
%{__install} -p -m 644 systemd/snapm-gc@.timer ${RPM_BUILD_ROOT}/%{_unitdir}

mkdir -p ${RPM_BUILD_ROOT}/%{_tmpfilesdir}
%{__install} -p -m 644 systemd/tmpfiles.d/%{name}.conf ${RPM_BUILD_ROOT}/%{_tmpfilesdir}/

%{__install} -d -m 0700 ${RPM_BUILD_ROOT}/%{_rundir}/%{name}
%{__install} -d -m 0700 ${RPM_BUILD_ROOT}/%{_rundir}/%{name}/mounts
%{__install} -d -m 0700 ${RPM_BUILD_ROOT}/%{_rundir}/%{name}/lock

%check
%pytest --log-level=debug -v tests/

%files
# Main license for snapm (Apache-2.0)
%license LICENSE
%doc README.md
%{_bindir}/snapm
%doc %{_mandir}/man*/snapm*
%attr(644, -, -) %config(noreplace) %verify(not md5 mtime size) %{_sysconfdir}/%{name}/snapm.conf
%attr(644, -, -) %config(noreplace) %verify(not md5 mtime size) %{_sysconfdir}/%{name}/plugins.d/*
%dir %attr(755, -, -) %{_sysconfdir}/%{name}/schedule.d
%attr(644, -, -) %{_unitdir}/snapm-create@.service
%attr(644, -, -) %{_unitdir}/snapm-create@.timer
%attr(644, -, -) %{_unitdir}/snapm-gc@.service
%attr(644, -, -) %{_unitdir}/snapm-gc@.timer
%attr(644, -, -) %{_tmpfilesdir}/%{name}.conf
%dir %{_rundir}/%{name}/
%dir %{_rundir}/%{name}/mounts
%dir %{_rundir}/%{name}/lock

%files -n python3-snapm
# license for snapm (Apache-2.0)
%license LICENSE
%doc README.md
%{python3_sitelib}/%{name}/
%{python3_sitelib}/%{name}*.dist-info/

%files -n python3-snapm-doc
# license for snapm (Apache-2.0)
%license LICENSE
%doc README.md
%doc doc

%changelog
* Sun Nov 16 2025 Bryn M. Reeves <bmr@redhat.com> - 0.5.2-1
- schedule: Fix COUNT policy off-by-many when keep_count > len(sets)
- snapm: make -c|--config mandatory for create-scheduled/gc
- snapm: report garbage collected snapshot set names
- schedule: fix timeline classification to allow multiple categories
- schedule: add gc decision debug logging
- snapm: add new snapm.schedule debug subsystem
- snapm: fix renaming of snapshot sets with boot entries

* Mon Nov 03 2025 Bryn M. Reeves <bmr@redhat.com> - 0.5.1-1
- dist: don't recursively set SELinux contexts for /run/snapm
- tests: coverage for Scheduler.edit()
- snapm: implement schedule edit command
- scheduler: add Scheduler.edit() method
- scheduler: add GcPolicy.has_params property
- scheduler: remove schedule from lists/dicts on Scheduler.delete()
- boot: use maxsplit=1 in BootEntryCache._parse_entry()
- boot: Replace asserts with explicit exceptions
- boot: Type the machine-id helper as Optional and align docstring
- command: Fix typo in print_schedules doc - 'separateed' → 'separated'
- command: Fix typo in create_schedule doc - 'autoinxed' → 'autoindex'
- command: Fix _revert_cmd docstring - 'Delete' instead of 'Revert'
- boot: drop `lvm_root_lv` docstring from _create_boom_boot_entry()
- boot: handle swap entries explicitly in create_snapset_boot_entry()
- boot: don't include swap entries in the snapset mount list
- snapm: fix formatting glitches in snapm.manager
- dist: add /run/snapm/lock to systemd/tmpfiles.d/snapm.conf
- manager: re-locate lock files to /run/snapm/lock
- dist: add tmpfiles.d drop-in for /run/snapm to snapm.spec
- snapm: add systemd/tmpfiles.d/snapm.conf
- snapm: add constants for runtime and mount dirs
- snapm: wire up new debug log filttering in snapm.command.setup_logging()
- snapm: convert modules to new log filtering interface
- snapm: replace SnapmLogger Logging class with SubsystemFilter Filter class
- snapm: use "snapm" rather than __name__ (snapm._snapm) for getLogger()
- snapm: add string constants for new name-based subsystem log filtering

* Mon Oct 06 2025 Bryn M. Reeves <bmr@redhat.com> - 0.5.0-1
- snapm: bump release to 0.5.0
- snapm: reject duplicates in Scheduler.create() sources argument
- snapm: introduce Manager-level locking
- snapm: add Manager.{activate,deactivate}_snapshot_set()
- snapm: require root priveleges for snapm commands
- schedule: write_config(): do not reference tmp_path when it is undefined
- tests: make git clones with --depth 1
- tests: update stratisd to stratisd-v3.8.5 and pin rust toolchain
- doc: note that lvm2cow does not support shrinking snapshots in snapm.8
- lvm2: add debug logging to size policy calculations
- lvm2: skip no-ops and use relative size in Lvm2Cow.resize_snapshot()
- lvm2: Detect and reject attempts to shrink CoW snapshots
- lvm2: round up SizePolicy.size byte values to lvm2 extent boundaries
- snapm: add size policy debug logging to create and resize
- lvm2: extend _Lvm2.vg_free_space() to return extent size
- lvm2: add lvm2._round_up_extents() to round sizes to extent boundaries
- lvm2: Check path args to _is_lv_device() for existence and major nr match
- lvm2: check for device-mapper major number before calling dmsetup
- tests: fix test_create_snapshot_set_no_provider to fail if !SnapmNoProviderError
- doc: don't drop PYTHONPATH entries - concatenate them like PATH
- doc: fix typo in snapm-schedule.d.5 man page
- snapm: fix typo in snapm.manager.plugins._plugin
- snapm: fix typos in snapm.manager._timers
- snapm: fix typos in snapm.wanager._schedule
- snapm: fix typos in snapm.manager._manager
- snapm: fix typos in snapm._snamp
- doc: tighten up examples and fix command synopsis
- doc: drop leading # from commands in *.md *.rst
- doc: clarify list, create and show JSON report structure and keys
- doc: use "snapshot set" vs. "snapset" consistently in snapm.8
- doc: document JSON key names for list vs. show output
- Update README.md and move detailed guide to doc/user_guide.rst
- snapm: require -p/--policy-type for 'snapm schedule create'
- snapm: make --yes/--no required and mutually exclusive for autoactivate
- tests: parallelize pylint and flake8 runs via --jobs
- tests: configure pycodestyle in setup.cfg
- tests: re-order python-basic-tests to run cheap tests first
- snapm: fix report field definition for snapshot_origin
- snapm: fix swapped _log_info() args in 'snapm snapset prune' output
- dist: add missing timer and service units to snapm.spec
- tests: set timeout-minutes=60 on python-basic-tests
- tests: add Ubuntu libvirt/BIOS workaround to virt_tests workflow
- virt_tests: add workaround to enable BIOS on Ubuntu libvirt
- tests: add virt_tests workflow to snapm.yml
- dist: exclude test paths from setup.cfg builds
- virt_tests: add end-to-end testing framework
- doc: add DeepWiki badge to README.md
- doc: fix remaining markup nits in snapm.8
- doc: improve example formatting for man2html in snapm.8
- doc: improve man2html compatibility (.UR/.UE) in snapm.8
- doc: fix --start option documentation in snapm.8
- doc: add missing -p|--policy-type option to OPTIONS in snapm.8
- doc: add missing -C|--calendarspec option in snapm.8
- doc: fix .P vs .IP errors in snapm.8
- doc: add missng "REPORT FIELDS: Schedules" subsection to snapm.8
- doc: add missng "REPORT FIELDS: Plugins" subsection to snapm.8
- doc: convert remaining examples to .EX/.EE in smapm.8
- doc: update 'snapm schedule list' example
- doc: make OPTIONS metavars consistent with SYNOPSIS in snapm.8
- doc: use macros for common argument groups in snapm.8
- doc: use schedule_name consistently as schedule name metavar
- doc: clarify that snapm schedule create does not accept --autoindex
- command: fix --rows help doc typo 'columnes'
- doc: fix additional consistency issues in command examples
- doc: fix 'schedule gc' -c/--config argument description
- doc: more man page markup improvements
- doc: mark sources as optional in CMD_SNAPSET_RESIZE in snapm.8
- snapm: enforce [ID] vs --*name/--*uuid exclusion via ArgumentParser
- snapm: make --uuid/--name higher precedence in Selection.from_cmd_args()
- snapm: make --uuid/--name and --snapshot-uuid/--snapshot-name mutually exclusive
- doc: add 'snapm snapset create --json' exmaple
- doc: use macros for common argument groups in snapm.8
- doc: use .UR/.UE for URIs in SEE ALSO of snapm.8
- doc: update man page date
- doc: document snapshot set/snapshot status once and crossref
- doc: use .BR instead of .MR for now (groff-1.23.0+ only)
- doc: fix 'scheduled' typo in snapm.8
- doc: avoid mixing manual font escapes inside .BR/.IR in snapm.8
- doc: add EXIT STATUS, FILES, and BUGS sections to snapm.8
- doc: fix font markup glitches in snapm.8
- doc: fix --boot option name in snapm.8 schedule create example
- doc: fix s/snapset/schedule/ for 'snapm schedule list' in snapm.8
- doc: add missing enable --start description to snapm.8
- doc: document additional uses of --json in snapm.8
- doc: document create-scheduled in snapm.8
- doc: snapm.8 s/rolling back/reverting/g
- doc: update snapm.8 for non-root boot changes
- doc: improve prose style in snapm.8
- doc: use .EX/.EE example markup in snapm.8
- doc: use .TP (tagged paragraph) with consistent indents in snapm.8
- doc: fix typos & consistency issues in `snapm.8`
- tests: make cleanup.sh more robust for Stratis pools
- snapm: support --json with {snapset,schedule} create commands
- doc: add CONTRIBUTING.md
- tests: add cleanup script to tests/bin
- tests: Add BootTestsBase and derive BootTestsNonRoot and BootTestsWithRoot
- boot: allow bootable snapsets without the root device
- Do not require wheel for building
- boot: drop explicit setting of `lvm_root_lv` when calling boom
- boot: add origin argument to _find_snapset_root()
- boot: always return path from _find_snapset_root()
- tests: improve manager module coverage
- manager: reject duplicate sources in Manager.create_snapshot_set()
- plugins: fix device detection for non-existent device paths
- loader: mark error branches as no cover
- tests: improve boot module coverage
- tests: improve calendar module coverage
- tests: improve command module coverage
- Schedule: clean up boot entries when garbage collecting snapshot sets
- tests: improve snapm module coverage
- tests: add SchedulerTests
- timer: only unlink drop-in files if present
- doc: add schedule commands to README.md
- doc: add man page command overview
- doc: add snapm-schedule.d.5
- doc: add 'snapm schedule' commands and arguments to snapm.8
- doc: fix some man page markup
- manager: make config loading a bit more efficient
- command: add `snapm schedule gc` command
- command: add gc_schedule() procedural wrapper
- command: add 'snapm snapset create-scheduled' command
- snapm: quote string when reporting malformed SizePolicy string
- command: add 'snapm schedule show' command
- command: add show_schedules() procedural wrapper
- command: add 'snapm schedule {enable,disable}' commands
- command: add {enable,disable}_schedule() procedural wrappers
- command: add `snapm schedule delete` command
- command: add delete_schedule() procedural wrapper
- command: add `snapm schedule create` command
- command: add schedule_create() procedural wrapper
- manager: export GcPolicy and CalendarSpec in snapm.manager
- command: factor out common create command arguments
- container_tests: add schedule module to coverage --include
- timers: fix missing f-string annotation in _timers._timer()
- container_tests: fix timer_tests exception args assertions
- command: add 'schedule' type subparer and 'schedule list' command
- command: add _schedule_list_cmd() handler
- command: add print_schedules reporting wrapper
- container_tests: create /etc/snapm/schedule.d in Containerfile
- snapm: add Selection support for Schedule objects
- command: add Schedule ReportObjType and FieldType list
- manager: export Schedule from snapm.manager
- timers: fix build on epel-9/cs9 due to py3.10 union notation
- manager: expose manager.scheduler interface for snapset scheduling
- manager: add snapm.manager.Scheduler interface for managing schedules
- schedule: add new module snapm.manager._schedule
- snapm: add datetime property to SnapshotSet
- timers: treat TimerStatus.RUNNING as enabled=True
- timers: convert to using snapm.SnapmSystemError for OsError derived errors
- snapm: add SnapmSystemError exception class
- timers: improve debug logging
- timers: ensure drop-in configuration overrides template unit
- doc: exclude __annotation__ from members documentation
- snapm: rename Snapshot and Snapshotset as_dict() methods as to_dict()
- doc: add snapm-plugins.d(5)
- doc: add snapm.conf(5)
- dist: add /etc/snapm to snapm.spec
- stratis: enforce configurable limits
- lvm2: enforce configurable limits
- plugin: add plugin limits infrastructure
- plugins: support optional configuration file
- manager: make plugin loading and snapshot discovery more robust
- manager: add configuration file support
- lvm2: use JSON lvs report format in vg_lv_from_device_path()
- lvm2: run callouts with LC_ALL=C
- lvm2: convert remaining calls to run() to use self._run()
- lvm2: make lvm2._avtivate() a method of _Lvm2
- lvm2: make lvm2.is_lvm_device() a method of _Lvm2
- lvm2: make lvm2._get_lvm_version() a method of lvm2._Lvm2
- lvm2: add _Lvm2._run() wrapper to enforce environment sanitization
- docs: apply workaround for sphinx-doc/sphinx#11000
- docs: disable unused html_static_path Sphinx config
- doc: fix doc/snapm.rst underline formatting
- snapm: fix snapm.manager._boot._create_default_os_profile() docstring
- docs: fix "mqnqger" typo in doc/snapm.rst
- docs: fix broken :param ...: docstring tag in Selection.check_valid_selection()
- docs: fix "tiemrs" typo in doc/snapm.rst
- dist: fix readthedocs.org builds (restore requirements.txt)
- doc: add snapm.manager private modules to docs
- timers: fix consstants in docstring description
- container_tests: add tests for high-level Timer interface
- timers: add high-level Timer interface
- timers: add timer status reporting
- timers: move _diable_timer() _remove_drop_in() into finally branch
- timers: add pragma: no cover to unreached DBus timeout branch
- timers: log debug-level message when writing drop-in file
- container_tests: update test_timers for private low-level interface
- timers: make the low-level interface private to _timers
- timers: add constant for drop-in directory path format
- calendar: make occurs, next_elapse, in_utc, from_now values dynamic
- timers: add 'pragma: no cover' annotations to exception branches
- tests: add initial timer tests
- tests: exclude container_tests/tests from regular snapm workflow
- tests: add container testing infrastructure
- manager: add systemd timer unit management interface
- dist: update systemd timer templates to always include OnCalendar
- snapm: add SnapmTimerError exception class
- manager: add debug logging to create_snapshot_set()
- tests: assert result of CommandTests.test_main_* tests
- tests: enable verbose logging for CommandTests*.test_main_* tests
- tests: add CommandTests*.get_debug_main_args() helper
- tests: fix test_main_snapset_create_autoindex
- calendar: add explicit CalendarSpec.original property
- calendar: prefer calendarspec original form
- calendar: fix docstring formatting
- dist: fix mixed-tabs-and-spaces in snapm.spec
- manager: move snapm imports ahead of .{_boot,_loader,_signals}
- manager: move snapm.manager.signals to snapm.manager._signals
- manager: move snapm.manager.calendar to snapm.manager._calendar
- manager: move snapm.manager.boot to snapm.manager._boot
- dist: use __install macro instead of directly invoking command
- Plugin: fix method docstring formatting
- dist: sync classifiers between pyproject.toml and setup.cfg
- dist: drop quotes around license value
- dist: drop obsolete python macro use
- dist: revise dependency versions
- dist: drop requirements.txt
- dist: restore install_requires
- dist: re-order BuildRequires and separate python and non-python deps
- dist: clean up Sphinx doc build and make unconditional
- dist: use install -p/--preserve-timestamps
- boot: tolerate missing boom.command.create_config()
- dist: pass test suite directory as positional arg to pytest macro
- dist: fix mixed tabs-and-spaces in snapm.spec
- dist: make version dynamic in pyproject.toml
- dist: consolidate dependencies in requirements.txt
- dist: drop unnecessary snapm/manager/pliugins/stratislib/LICENSE
- dist: add pytest to build requirements
- tests: use pytest instead of directly calling pytest-3
- dist: drop centos/rhel special casing for python build/install
- dist: change snapm license from GPL-2.0-only AND Apache-2.0 to Apache-2.0
- report: Row - stop misusing class vars
- report: Report - stop misusing class vars
- report: Field - stop misusing class vars
- report: FieldType - stop misusing class vars
- report: ReportOpts - stop misusing class vars
- report: ReportObjType - stop misusing class vars
- report: FieldProperties - stop misusing class vars and add initialiser
- snapm: move Plugin into snapm.manager.plugins
- snapm: drop PluginRegistry metaclass
- snapm: refactor plugin loader and move to snapm.manager._loader
- dist: avoid Python packaging warnings
- snapm: unify filtering for valid characters in names
- snapm: fix Manager.split_snapshot_sets() with empty sources
- command: add new 'snapm snapset prune' command
- command: add new 'snapm snapset split' command
- tests: add Manager.split_snapshot_set() tests
- snapm: add Manager.split_snapshot_set()
- snapm: include state string in exception raised from _check_snapset_status()
- snapm: add snapm.SnapmArgumentError exception class
- doc: add --autoindex and basename, index fields to README.md
- doc: add --autoindex and index, basename fields to snapm(8)
- snapm: add snapm snapset create --autoindex
- snapm: add report field types for basename and index to snapm.command
- report: add new REP_IDX field data type for index values
- snapm: add constants for basename and index property names
- snapm: add 'autoindex' kwarg to snapm.command.create_snapset()
- snapm: add autoindex support to Manager.create_snapshot_set()
- snapm: add missing param docstrings to Manager.create_snapshot_set()
- snapm: Allow finding snapshot sets by basename and index
- snapm: support SnapshotSet names with basename and index
- tests: add missing '[cache]' section to test boom.conf
- snapm: refresh boot cache after creating entries in create_snapshot_set()
- snapm: improve SnapshotSet action method logging
- snapm: use Manager._set_autoactivate() in create_snapshot_set()
- Manager: use common snapm.bool_to_yes_no() in Manager.set_autoactivate()
- Manager: factor out common code from Manager.set_autoactivate()
- snapm: move _bool_to_yes_no() from snapm.command to snapm
- Plugins: add generic Plugin.autoactivate setter and drop set_autoactivate()
- Manager: fix autoactivate setting during snapshot set creation
- snapm: move snapshot -> snapset linkage into SnapshotSet()
- dist: replace license classifier with SPDX expressions
- tests: add python3-pytest-subtests to Ubuntu dependencies
- dist: add template systemd units for scheduler
- tests: also install faketime binary on Ubuntu
- tests: skip test_calendar_spec_next if faketime is missing
- dist: add BuildRequires: libfaketime for Fedora builds
- tests: refactor CalendarSpec tests to use unittest.subTest()
- tests: remove debug statements from test_calendar.py
- tests: add CalendarSpec unit tests
- tests: add 'libfaketime' package to Ubuntu workflow dependencies
- tests: disable pylint too-many-positional-arguments check
- snapm: add snapm.manager.calendar and CalendarSpec class
- snapm: raise SnapmNotFoundError if source path does not exist
- tests: switch GitHub workflows over to Stratis 3.8.0
- tests: install stratis-cli from git and pin version to 3.7.0
- lvm2: filter out LVM2 environment variables when calling lvm commands
- lvm2: make vg_lv_from_device_path a method of Lvm2Cow and Lvm2Thin
- lvm2: ensure vg_lv_from_device_path() gets the expected report format
- lvm2: determine LVM2 JSON format argument based on installed version
- lvm2: refactor helper functions as Lvm2 plugin methods
- tests: allow mock vgs/lvs to accept --reportformat json or json_std
- dist: clean up copyright statements and convert to SPDX license headers
- dist: update GPLv2 text in COPYING
- snapm: factor out common code for snapshot origin and mount point links
- snapm: move rename logic from Manager to SnapshotSet.rename()
- snapm: move resize logic from Manager to SnapshotSet.resize()
- snapm: move {de,}activate logic from Manager to SnapshotSet.{de,}activate()
- snapm: move revert logic from Manager into SnapshotSet.revert()
- snapm: move boot entry creation inside Manager.create_snapshot_set()
- manager: replace open-coded snapset delete with snapset.delete()
- snapm: implement SnapshotSet.delete()
- snapm: catch KeyboardInterrupt in snapm.command.main()
- snapm: decorate state-changing Manager methods with @suspend_signals
- snapm: add @suspend_signals decorator to block SIGINT, SIGTERM
- report: strip trailing whitespace from report output
- tests: bracket test cases with log messages
- tests: add explicit log formatter to file and console handlers
- tests: capture tool output and log command execution
- tests: add console handler to test suite logger
- snapm: avoid duplicating log handlers when main() is called repeatedly
- tests: fix duplicate log handlers in test suite
- dist: use {name} instead of hardcoding snapm in URL and autosetup
- jenkins: add file to list dependencies of python test suite
- tests: enable workflow on pull_request
- tests: temporarily exclude CentOS from test_boot.is_redhat() check
- dist: move python3-boom dep to python3-snapm and require >= 1.6.4

* Thu Dec 12 2024 Bryn M. Reeves <bmr@redhat.com> - 0.4.0
- stratis: return False for non-DM devices from is_stratis_device()
- command: accept zero or more source spec arguments to snapset resize
- doc: add snapset resize command to README.md
- snapm: disable recursive snapshot sets (snapshots-of-snapshots)
- doc: update examples in README.md
- doc: update examples in snapm.8
- snapm: Manager.delete_snapshot_sets(): fix formatting
- snapm: simplify is_size_policy() and re-use SizePolicy validation
- tests: additional tests for size policy validation
- snapm: use SnapmSizePolicyError consistently for size policy validation
- snapm: add missing SnapmSizePolicyError to snapm.__all__
- snapm: check for block device sources that duplicate mount point sources
- snapm: suppress redundant fields in SnapshotSet string output
- doc: update README.md for block device snapshot support
- doc: update snapm.8 for block device snapshot support
- doc: add Stratis project URL to snapm.8
- snapm: make 25 percent SIZE the default size policy for block device snapshots
- tests: fix mock dmsetup for test_is_stratis_device
- snapm: fix detection of unmounted block devices for USED size policy
- tests: extend coverage for block device snapshot sets
- tests: extend test_create_snapshot_set_blockdevs to Stratis devices
- stratis: accept /dev/stratis/$POOL/$FS form in is_stratis_device()
- tests: merge ManagerBlockdevTests into ManagerTests
- command: convert command line arguments from 'mount_points' to 'sources'
- command: convert resize_snapset() from mount_points to sources
- command: convert create_snapset() from mount_points to sources
- manager: disable lints on Manager.create_snapshot_set()
- lvm2: fix size_map indexing on resize
- manager: accept mount point or device sources in resize_snapshot_set()
- manager: accept mount point or device sources in create_snapshot_set()
- manager: generalise _find_and_verify_plugins() for block device sources
- manager: generalise _parse_mount_point_specs()
- manager: add _find_mount_point_for_devpath() helper
- snapm: add devices FieldType to snapshot set reports
- snapm: add Devices to SnapshotSet string and JSON representation
- command: replace 'mountpoint' with 'source' in default snapshot report
- command: replace 'mountpoints' with 'sources' in default snapset report
- command: add source FieldType to snapshot reports
- command: add sources FieldType to snapshotset reports
- snapm: add SnapshotSet.devices property
- snapm: move 'SnapshotSet.sources' ahead of 'SnapshotSet.mount_points'
- snapm: add Source to Snapshot string and JSON representation
- snapm: add Sources to SnapshotSet string and JSON representation
- snapm: add SnapshotSet.snapshot_by_source()
- snapm: add Snapshot.source and SnapshotSet.sources properties
- plugins: rename Plugin.can_snapshot() argument to 'source'
- stratis: fix errors in Stratis._check_free_space() docstring
- lvm2: index size_map by lv_name instead of mount_point
- snapm: handle non-mount points in mount_point_space_used()
- snapm: restrict USED SizePolicy to mounted sources
- lvm2, stratis: pass origin directly to Plugin._check_free_space()
- lvm2, stratis: always include '/dev' prefix in origin
- lvm2: include stderr in exceptions raised from failed callouts
- snapm: fix lints in snapm.manager
- snapm: check that source paths are either a mount point or block device
- lvm2: accept paths in /dev/vg/lv format in vg_lv_from_device_path()
- add support for block devices as a snapshot source
- tests: move logging initialisation after local imports
- tests: extend test coverage to Stratis plugin
- snapm: fix license header typo
- dist: add COPYING.stratislib to RPM spec file
- tests: use 'udevadm settle' after starting Stratis pool
- tests: don't capture stratis stdout/stderr
- tests: move logging initialisation after local imports
- tests: use upstream stratisd-3.7 branch for testing
- tests: extend test coverage to Stratis plugin
- tests: add StratisLoopBacked.{stop,start}_pool()
- tests: add infrastructure for Stratis plugin tests
- stratis: add resize support
- stratis: add revert support to Stratis plugin
- stratis: add Stratis plugin
- tests: build and install stratisd and stratis-cli in CI
- plugins: add stratislib infrastructure for Stratis plugin
- tests: move simple tests to CommandTestsSimple
- tests: test resizing fails when out-of-space
- tests: test resize of non-member mount point fails
- snapm: resume journal in Manager.create_snapshot_set() error branch
- tests: add bad name/uuid tests for revert and resize
- manager: factor out common code for name/uuid lookup
- tests: test rename error and rollback action
- tests: check that an error deleting snapshots raises SnapmPluginError
- snapm: remove snapshot set members on successful delete
- snapm: check for REVERTING state when deleting snapshot sets
- tests: check that deleting a mounted snapshot set fails
- tests: test that revert, rename or delete on a reverting snapset fails
- snapm: invalidate cache on Snapshot.revert()
- tests: call 'lvs -a' in LvmLoopBacked.dump_lvs()
- snapm: mark _{suspend,resume}_journal() error paths as no cover
- doc: add 'snapset resize' subcommand to snapm.8
- doc: add missing 'revert' subcommand to ARG_SNAPSET_COMMANDS
- tests: improve coverage for Plugin base class
- tests: Manager.resize_snapshot_set() tests
- command: add snapset resize command
- snapm: add Manager.resize_snapshot_set() method
- snapm: add Snapshot.check_resize() and Snapshot.resize() methods
- lvm2: add Lvm2Thin.{check_,}resize_snapshot()
- lvm2: add Lvm2Cow.{check_,}resize_snapshot()
- snapm: add Plugin.{check_,}resize_snapshot()
- snapm: make Snapshot provider member public
- snapm: fix Manager.revert_snapshot_set() docstring comment
- lvm2: re-check LVM name in Lvm2Thin.create_snapshot()
- lvm2: fix lvm2thin free space check
- lvm2: check for INVALID status before REVERTING
- doc: add reverting status to snapm.8
- snapm: check for invalid status before reverting
- snapm: check for INVALID or REVERTING snapset status before operations
- lvm2: report REVERTING status for merging snapshot volumes
- lvm2: extend lvm2cow and lvm2thin plugins to include merging snapshots
- lvm2: extend snapshot filtering to include merging snapshots
- lvm2: add lvs_all keyword argument to lvm2.get_lvs_json_report()
- snapm: add 'Reverting' status to SnapStatus
- snapm: index Manager.by_uuid by UUID object rather than str(uuid)
- snapm: validate UUIDs during argument parsing
- lvm2: fix Snapshot initialisation after Lvm2Plugin.rename_snapshot()
- snapm: check for mounted snapshots before attempting to delete set
- snapm: fix Snapshot.snapshot_mounted property
- lvm2: don't capture lvconvert output during --merge operation
- snapm: fix exception when reverting unmounted snapset
- tests: make lvm2 test cleanup more robust
- lvm2: log debug message when renaming snapshot
- manager: handle errors when initialising plugins
- manager: call origin_from_mount_point() once per mount point during create
- snapm: check for ability to revert before reverting snapset

* Thu Jul 25 2024 Bryn M. Reeves <bmr@redhat.com> - 0.3.1
- tests: add test for mount_point_space_used()
- tests: add tests for device_from_mount_point()
- tests: add invalid snapshot names to parse_snapshot_name() tests
- tests: add escaped strings to encode/decode_mount_point tests
- tests: support 'dmsetup info -c --noheadings -ouuid' in mock dmsetup
- snapm: catch exceptions when importing plugins
- plugins: move DMSETUP constants to snapm.manager.plugins
- plugins: move DEV_MAPPER_PREFIX to snapm.manager.plugins
- snapm: remove unused logging import from plugin modules
- plugins: fix plugin logging
- plugins: move DEV_PREFIX constant to snapm.manager.plugins
- plugins: make plugin internal constants private
- snapm.manager: convert ImporterHelper._find_plugins_in_dir() to function
- lvm2: convert Lvm2Cow._snapshot_min_size() into function
- snapm.manager: convert ImporterHelper._get_plugins_from_list() into function
- lvm2: define _Lvm2.max_name_len
- lvm2: generalise _check_lvm_name() between Lvm2CoW/Lvm2Thin
- lvm2: log plugin type in Lvm2{CoW,Thin}.can_snapshot() error message
- lvm2: convert _Lvm2._activate() into a function
- snapm.manager.boot: rename single character locals in _get_machine_id()
- snapm.manager: convert Manager._parse_mount_point_specs() into function
- snapm.manager: convert Manager._{suspend,resume}_journal() into functions
- snapm.manager: convert ImportHelper._plugin_name() into function
- snapm.manager: call super().__init__() from PluginRegistery.__init__()
- snapm.command: rename 'ws' local to 'wspace'
- tests: use pip to install boom-boot in CI
- test: enable pylint in CI
- test: add .pylintrc to git
- snapm.manager.boot: replace FIXME with Note
- snapm.command: disable global-statement pylint warning
- snapm.command: factor out subparser addition from snapm.command.main()
- snapm: disable more pylint checks
- report: merge comparisons in Report.{__get_field,__key_match}()
- dist: handle py3_install/pyproject_install differences in files
- dist: add BuildRequires for python3-pip and python3-wheel
- dist: fix changelog date in snapm.spec
- snapm: fix snapm.command._log_warn definition
- dist: use py3_build/py3_install on RHEL/CentOS
- snapm: refactor Manager.create_snapshot_set()
- manager: flush and suspend journal before creating snapshots
- report: replace open coded comparison with max() in __recalculate_fields()
- report: use .items() to iterate over SHAs in __recalculate_sha_width()
- lvm2: add abstract Lvm2Snapshot.free() method definition
- snapm.manager.boot: add missing docstring to BootCache.refresh_cache()
- snapm.manager.boot: drop unused arg from create_snapset_boot_entry()
- snapm: drop unused arg and fix docstring for _create_boom_boot_entry()
- snapm.manager.boot: change if to bool() in _create_boom_boot_entry()
- snapm.manager.boot: explicitly re-raise exceptions in _create_boom_boot_entry()
- snapm.manager.boot: catch OSError instead of Exception in _get_machine_id()
- snapm.command: mark selection argument as unused in print_plugins()
- snapm.manager: add missing docstrings to boot entry creation methods
- snapm.manager: explicitly re-raise exceptions in deactivate_snapshot_sets()
- snapm.manager: explicitly re-raise exceptions in activate_snapshot_sets()
- snapm.manager: explicitly re-raise exceptions in revert_snapshot_set()
- snapm.manager: explicitly re-raise exceptions in delete_snapshot_sets()
- snapm.manager: explicitly re-raise exceptions in rename_snapshot_set()
- snapm.manager: explicitly re-raise exceptions in create_snapshot_set()
- snapm.manager: use lazy formatting in create_snapshot_set() debug
- snapm: set encoding in open() in Snapshot.{origin,snapshot}_mounted
- snapm: add missing Snapshot.json() docstring
- snapm: use generator in SnapshotSet.{origin,snapshot}_mounted
- snapm: remove unnecessary elif/else in SizePolicy.size()
- snapm: remove unnecessary else in SizePolicy._parse_size_policy()
- snapm: mark percent variable unused in snapm.is_size_policy()
- snapm: remove unnecessary else in snapm.is_size_policy()
- snapm.report: only import dumps from json
- snapm.command: remove unnecessary class variables from ReportObj
- snapm: break up long string in SizePolicy._parse_size_policy()
- tests: add flake8 to GitHub workflow
- snapm.report: remove unused math import
- dist: add E203 to flake8 ignore list
- snapm: disable flake8 F401, F403 in snapm/manager/plugins/__init__.py imports
- snapm.manager.boot: remove unused exception variable
- snapm.manager.boot: remove unused import SnapmInvalidIdentifierError
- snapm: fix by_name/by_uuid references in Manager.revert_snapshot_set()
- snapm: disable flake8 F401, F403 in snapm/manager/__init__.py imports
- snapm: fix UUID handling in snapm.command._revert_cmd()
- snapm: remove unused variable in snapm.is_size_policy()
- dist: add flake8 configuration to setup.cfg
- snapm: disable flake8 F401, F403 in snapm/__init__.py imports
- snapm.command: remove unnecessary imports
- dist: add libdbus-1-dev to apt_packages in .readthedocs.yaml
- dist: enable Python deps in .readthedocs.yaml
- snapm: generalise revert check for mounted fs to snapshot and origin
- dist: add boom-boot to RPM Recommends

* Thu Jun 20 2024 Bryn M. Reeves <bmr@redhat.com> - 0.3.0
- dist: add boom-boot to RPM Recommends
- snapm: forbid revert if snapset status is SnapStatus.INVALID
- dist: add pyproject.toml
- tests: fix boot path in tests/boot/boom/boom.conf
- snapm: check for boom configuration and create if missing
- dist: add dependency on boom-boot >= 1.6.4
- docs: update list of Packit build targets in README.md
- doc: add JSON output options to snapm.8
- doc: add and update command output examples to README.md
- dist: add requirements.txt
- doc: add boom requirement to installation instructions
- snapm: fix plugin list command formatting (black)
- tests: add JSON report output test
- report: fix setting of ReportOpts.json in ReportOpts()
- report: allow sort keys to use optional prefix
- report: handle $PREFIX_all when parsing field list
- tests: fix ReportTests::test_ReportOpts_str() for JSON output
- snapm: add support for JSON reports to list commands
- report: add support for JSON output
- snapm: add snapset_name as a default field for snapshot reports
- report: add title argument to Report()
- report: remove unused 'private' Report argument
- report: fix field prefix handling and drop hardcoded snapshot_ prefix
- report: fix comment typos
- dist: add BuildRequires for boom-boot to snapm.spec
- snapm: don't use match statement for compatibility with py3.9
- dist: build for epel and centos-stream in .packit.yaml
- lvm2: fix unpacking of snapshot name fields in Lvm2Cow
- snapm: add 'plugin' subcommand to display info on available plugins
- snapm: rename snapshot cmd handler fns to better match command line
- doc: describe size policy for non-fixed sized snapshot types in snapm.8
- doc: add default --size-policy to README.md
- doc: describe size checks for non-fixed sized snapshots in README.md
- tests: add command-level size policy tests
- tests: add integration tests with default and per-mount size policy
- lvm2: apply SizePolicy checks to thin volumes
- doc: provide a better description of --nameprefixes in snapm.8
- lvm2: apply name length checks separately for Lvm2Cow/Lvm2Thin
- snapm: check for in-progress revert when creation snapshot sets
- snapm: add Bootable property to sets to indicate if boot configured
- snapm: add missing autoactivate state to SnapshotSet.__str__ and json
- snapm: use Manager.revert_snapset() for 'snapm snapset revert'
- snapm: split Manager.revert_snapset() out from revert_snapsets()
- snapm: warn user to reboot when reverting with an in-use origin
- snapm: add SnapshotSet.origin_mounted and Snapshot.origin_mounted
- snapm: rename local in Manager.rename_snapshot_set() revert->rollback
- snapm: include timestamp in SnapshotSet and Snapshot json
- snapm: handle zero values in snapm.size_fmt()
- snapm: use constants for properties and report fields
- tests: add json property to MockArgs
- snapm: use lists rather than discrete JSON blobs for multi-item output
- snapm: add --json option to snapset and snapshot show commands
- snapm: add .as_dict() and .json() methods to SnapshotSet and Snapshot
- snapm: use constants for SnapshotSet and Snapshot property names
- snapm: improve formatting of Snapset boot entries
- snapm: include kernel version in boot entry titles
- snapm: rename "rollback" to "revert"
- Fix test_auto_profile_create_boot_entry
- snapm: imrpove snapset delete log message
- lvm2: limit maximum name length to max lvm name - 4 (123) for CoW
- snapm: improve error logging in Manager.create_snapshot_set()
- lvm2: check lvm name limits in Lvm2Thin.check_create_snapshot()
- snapm: improve rollback command log message
- snapm: refresh boot cache after operations that may invalidate it
- snapm: delete snapshot boot entry when rolling back snapset
- boot: force root fs to be rw when creating snapshot boot entry
- tests: set correct initramfs and kernel patterns for Ubuntu boom profile
- boot: ensure boom image cache is cleaned when deleting entries
- boot: enable boom image caching when creating boot entries
- doc: update man page date in snapm.8
- doc: add description of booting and rolling back sets to snapm.8
- doc: update examples in snapm.8
- doc: add missing snapset report fields to snapm.8
- doc: fix description of -o|--options in snapm.8
- doc: add break after size policy example in snapm.8
- doc: describe default size policy in snapm.8
- doc: fix create command description in snapm.8
- doc: fix typos in snapm.8 SNAPSHOT SETS AND SNAPSHOTS
- doc: Update valid characters for snapset names in snapm.8
- snapm: use rsplit() to parse size policy from mount spec
- snapm: raise SnapmPluginError in more Manager methods
- snapm: add SnapmPluginError and improve error message on set create
- plugins: escape invalid characters in mount point path
- doc: add size and free snapshot fields to snapm.8
- doc: add autoactivate, bootentry and rollback to report field list
- doc: document --members option in snapm.8 man page
- snapm: fix formatting of _DEFAULT_SNAPSHOT_FIELDS
- snapm: uncouple display snapset members from --verbose
- snapm: don't display extra fields in --verbose reports
- snapm: fix whitespace in snapset/snapshot show subcommands
- snapm: change default snapshot report fields
- doc: add size policies to snapm.8 man page
- snapm: add size and free to string representation of snapshots
- snapm: move size_fmt() from snapm.report to snapm
- snapm: add report fields for size_bytes and free_bytes
- snapm: add Snapshot.free to reflect free space available to snapshots
- snapm.SizePolicy: always return a multiple of sector size
- snapm: improve formatting of capped percentage size policy checks
- snapm: add size field to the snapshot report
- report: add REP_SIZE field type for human readable size strings
- snapm: add a size property to Snapshot
- snapm.SnapshotSet: remove useless class variables
- snapm.Snapshot: remove useless class variables
- snapm: command line support for default size policy
- snapm: support default size policy in Manager.create_snapshot_set()
- snapm: also cap FREE size policies to <=100
- tests: test snapm.is_size_policy()
- lvm2: fix free space accounting for multiple VGs
- tests: add basic tests for SizePolicy class
- doc: add basic documentation for size policies to README.md
- tests: fix ManagerTests::test_create_snapshot_set_no_space_raises
- lvm2: fix free space accounting in Lvm2Cow._check_free_space()
- snapm: cap PERCENT_SIZE policy to 100 of device size
- snapm: fix FREE space accounting
- Add Plugin.{start,end}_transaction()
- Make default SizePolicy 200USED
- snapm: implement size control policies in manager and plugins
- snapm: add size control policy infrastructure
- lvm2: improve error message when snapshot creation fails

* Thu May 30 2024 Bryn M. Reeves <bmr@redhat.com> - 0.2.1
- tests: set boom /boot path explicitly in test_boot
- tests: skip privileged tests if running as non-root user
- tests: use mock /boot directory for command and manager tests
- tests: replace zero-length find targets with dummy files
- Update snapm.spec source to use URL with name and version macros
- Change interpreter to /usr/bin/python3
- Use _smp_mflags make flags in snapm.spec docs build
- Fix use of python3_sitelib wildcard in snapm.spec
- Fix license and classifiers in setup.cfg
- Set boom optional keys defaults if enabled in profile
- Use v4 of checkout action in GitHub workflow

* Mon May 20 2024 Bryn M. Reeves <bmr@redhat.com> - 0.2.0
- Bump release and update .packit.yaml
- Require python3-boom
- Set default boom profile options from /proc/cmdline
- Only pass no_fstab=True if command line mounts are defined
- Set lvm_root_lv when calling boom.create_entry() for LVM volumes
- Broaden exception handling in 'snapm snapset create'
- Set and verify autoactivate when creating bootable snapshot set
- Move autoactivation status change into SnapshotSet class
- tests: skip profile auto-creation test on Ubuntu
- Ignore bandit B101, "assert used"
- tests: add boot integration regression tests
- Add test support to allow users of LvmLoopBacked to create snapshots
- Add LvmLoopBacked.dump_vgs() to assist debugging LVM tests
- Update README.md for --bootable and --rollback
- Document -b|--bootable and -r|--rollback options in man page
- Add boot integration using the boom boot manager
- Add -m to coverage report command to report missing statements
- Combine regular and coverage test runs into a single step
- Add azure boom OsProfile for CI testing hosts
- tests: extend test workflow to install boom
- Fix comment in tests/_util.py
- Add test to assert that SnapmNoSpaceError is raised when VG out of space
- Increase the test loop device size to 12GiB
- Propagate exceptions from Manager.create_snapshot_set()
- Add snapset rollback command to man page and README.md
- Add test for "snapm snapset rollback" command
- Add test for Manager.rollback_snapshot_sets()
- Add infrastructure for roll back tests
- Add snapm snapset rollback command
- Add rollback support to Manager, Snapshot and Plugins
- Move snapm.command._expand_fields()
- Split coverate reporting into a separate task step
- Try to exclude python-dbus modules from CI coverage runs
- Redefine Snapshot.origin and add Snapshot.origin_options property
- Properly calculate minimum unique SHA prefix in reports
- doc: fix man page typo ("columnes")
- manager: add ' ' to the list of forbidden characters for snapset names
- dist: use packages = find: in setup.cfg

* Fri Nov 17 2023 Bryn M. Reeves <bmr@redhat.com> - 0.1.0
- Initial spec file for packaging
