# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Snapm (Snapshot Manager) is a Python CLI tool for managing coordinated snapshot sets across multiple Linux storage volumes (LVM2 CoW, LVM2 Thin, Stratis). It integrates with [boom-boot](https://github.com/snapshotmanager/boom-boot) for bootable snapshot entries and supports automated snapshot scheduling via systemd timers.

## Build & Install

```bash
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
python3 -m pip install -e .
```

Or run directly from a clone:
```bash
export PATH="$PWD/bin:$PATH" PYTHONPATH="$PWD:$PYTHONPATH"
```

## Linting

All three must pass (CI runs them in this order):
```bash
pycodestyle snapm
flake8 --jobs auto snapm
pylint --jobs 0 snapm
```

Format library code with `black` (tests are excluded from auto-formatting).

## Running Tests

**Tests require root** — they create LVM2/Stratis devices via loopback. Run in a VM or isolated environment. Needs ~250 MiB free in `/var/tmp`. Full run takes ~25-30 minutes.

Before first run, install system config:
```bash
sudo cp -r etc/snapm /etc
sudo cp systemd/*.service systemd/*.timer /usr/lib/systemd/system/
sudo cp systemd/tmpfiles.d/snapm.conf /usr/lib/tmpfiles.d/
sudo systemd-tmpfiles --create /usr/lib/tmpfiles.d/snapm.conf
sudo systemctl daemon-reload
```

Run entire suite with coverage:
```bash
sudo coverage run -m pytest -v --log-level=debug tests/
coverage report --include "./snapm/*"
```

Run a single test:
```bash
sudo pytest -v --log-level=debug tests/ -k <test_name_pattern>
```

Container tests (requires `podman`):
```bash
make -C container_tests clean && make -C container_tests all && make -C container_tests report
```

Clean up after failed tests: `tests/bin/cleanup.sh --force`

## Architecture

### Package Structure

- `snapm/_snapm.py` — Core types and constants: `SnapshotSet`, `Snapshot`, `Selection`, `SnapStatus`, exception hierarchy (`SnapmError` and subclasses), size parsing, UUID namespaces, debug subsystem masks.
- `snapm/command.py` — CLI entry point and procedural API. Argparse-based command routing for `snapset`, `snapshot`, and `schedule` subcommands. Called from `bin/snapm`.
- `snapm/report.py` — Generic tabular/JSON reporting engine modeled after device-mapper's reporting system. Defines field types (`REP_STR`, `REP_NUM`, `REP_SHA`, `REP_SIZE`, etc.) and supports custom column selection and multi-column sorting.

### Manager & Plugin System

- `snapm/manager/_manager.py` — `Manager` class: the central orchestrator. Loads plugins, discovers existing snapshots, and coordinates snapshot set creation/deletion/revert as transactions across plugins. Uses file locking (`fcntl`) for concurrency and signal suspension for atomicity.
- `snapm/manager/_loader.py` — Dynamic plugin discovery. Scans `snapm/manager/plugins/` for non-underscore-prefixed `.py` files, imports them, and finds `Plugin` subclasses.
- `snapm/manager/plugins/_plugin.py` — `Plugin` ABC. New storage backends must subclass this and implement: `discover_snapshots`, `can_snapshot`, `check_create_snapshot`/`create_snapshot`, `rename_snapshot`, `check_resize_snapshot`/`resize_snapshot`, `check_revert_snapshot`/`revert_snapshot`, `delete_snapshot`, `activate_snapshot`/`deactivate_snapshot`, `set_autoactivate`, `origin_from_mount_point`.
- `snapm/manager/plugins/lvm2.py` — LVM2 plugin (both CoW and Thin). Uses `dmsetup`, `lvs`, `lvcreate`/`lvremove` commands.
- `snapm/manager/plugins/stratis.py` — Stratis plugin. Communicates with stratisd via D-Bus (using `dbus-client-gen`/`dbus-python-client-gen`).

Plugin configuration lives in `etc/snapm/plugins.d/` (INI format with `[Limits]` and `[Priority]` sections).

### Scheduling & Boot

- `snapm/manager/_schedule.py` — `Schedule` and `GcPolicy` classes for automated snapshot creation with retention policies.
- `snapm/manager/_calendar.py` — `CalendarSpec` parser (systemd-style calendar expressions).
- `snapm/manager/_boot.py` — boom-boot integration for creating snapshot/revert boot entries.
- `snapm/manager/_timers.py` — systemd timer management for scheduled snapshots and garbage collection.

### Filesystem Diff Engine (`snapm/fsdiff/`)

Compares snapshot sets to detect filesystem changes. Pipeline: `FsDiffer` (entry point) → `treewalk` (parallel filesystem walking) → `changes` (change detection with move tracking) → `engine` (aggregation into `FsDiffResults`/`FsDiffRecord`) → `tree` (hierarchical tree output). Also: `contentdiff` (unified diff of file contents), `cache` (caching layer), `options` (`DiffOptions` configuration).

### Test Structure

- `tests/` — Main pytest suite. Tests mirror module structure (`test_lvm2.py`, `test_stratis.py`, `test_manager.py`, etc.). `tests/_util.py` provides `LoopBackDevices`, `LvmLoopBacked`, and `StratisLoopBacked` helpers that create real storage for integration tests.
- `tests/fsdiff/` — Dedicated fsdiff engine tests.
- `virt_tests/` — Full VM-based integration tests using libvirt/QEMU. Run in CI across firmware (BIOS/UEFI) × storage (LVM/LVM-thin) × distro matrices.
- `container_tests/` — Podman container-based tests.

Test pool/VG names use checksummed unique names via `generate_test_name()` in `tests/_util.py`.

## Commit Message Format

```
subsystem: description of change
```

Subsystem is typically the module name (e.g. `lvm2`, `stratis`, `fsdiff`), package name (`plugins`, `manager`), or one of: `snapm` (tree-wide), `doc`, `tests`, `dist`, `scripts`. Reference issues with `Related: #N` or `Resolves: #N`.

## AI Policy

When committing AI-assisted code, include in the commit message:
```
Assisted-by: Tool Name <https://tool.example.com>
```

## Coding Conventions

- All functions and methods require Sphinx-format docstrings.
- Logging pattern: each module creates `_log = logging.getLogger(__name__)` with `_log_debug`, `_log_info`, `_log_warn`, `_log_error` aliases. Subsystem-specific debug uses wrapper functions (e.g. `_log_debug_fsdiff`) with `extra={"subsystem": ...}`.
- `.pylintrc` disables: C0302 (too-many-lines), R0902 (too-many-instance-attributes), R0903 (too-few-public-methods), R0913 (too-many-arguments), R0801 (duplicate-code), R0917 (too-many-positional-arguments).
- `pycodestyle`/`flake8` ignore E501 (line length), E203 (whitespace before ':'), W50x.
