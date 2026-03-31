==========
User Guide
==========

This guide provides user documentation for the snapshot manager (snapm)
tool.

Overview
========

Snapshot manager (snapm) is a tool for managing coordinated sets of
snapshots on Linux systems. The snapm tool allows snapshots of multiple
volumes to be captured at the same time, representing the system state
at the time the snapshot set was created.

The tool has a modular plugin architecture allowing different snapshot
backends to be used together. Currently snapshots using LVM2
copy-on-write as well as thinly provisioned snapshots using both LVM2
and Stratis are supported.

Key Concepts
============

Snapshot Sets
-------------

Snapshot manager groups snapshots taken at the same moment in time into
a named "snapshot set", or snapset. This allows the snapshots to be
managed as a single unit and simplifies the management of snapshots of
multiple volumes. Each snapshot set must contain at least one snapshot.

Size Policies
-------------

Size policies control the size of fixed-sized snapshots and check for
available space when creating a snapshot set.

Some snapshot implementations (Lvm2CoW) require a fixed size to be
specified for the snapshot backstore when the snapshot is created. The
default size allocated by snapshot manager is 2x the current file system
usage, allowing the existing content of the volume to be overwritten
twice before exhausting the snapshot space.

Plugins for snapshot providers that do not require a fixed snapshot size
will check that space is available for the requested size policy at
snapshot set creation time.

Available size policies:

* **FIXED** - a fixed size given on the command line
* **FREE** - a percentage fraction of the free space available
* **USED** - a percentage fraction of the space currently used on the
  mount point (may be greater than 100%)
* **SIZE** - a percentage fraction of the origin device size from 0 to
  100%

The FIXED size policy accepts optional units of KiB, MiB, GiB, TiB, PiB,
EiB and ZiB. Units may be abbreviated to the first character.

The USED size policy can only be applied to mounted file systems.

Mount Manager
=============

The Mount Manager provides a convenient way to access the contents of a
snapshot set without needing to reboot or manually mount individual
snapshot volumes. This is particularly useful for inspecting files,
recovering data, running commands against the snapshotted state, or
comparing configurations.

How it Works
------------

When a snapshot set is mounted, ``snapm`` performs the following steps:

1. Creates a mount point at ``/run/snapm/mounts/<snapset_name>``
2. Mounts the root snapshot volume to this location
3. Mounts additional snapshot volumes from their corresponding
   snapshot devices to the appropriate paths within the hierarchy
4. Bind-mounts API and runtime file systems (such as ``/proc``,
   ``/sys``, and ``/dev``) from the host system
5. Bind-mounts non-snapset file systems (such as ``/boot``) from
   the host system

This creates a complete, consistent view of the system as it existed
at the time the snapshot set was created (with the exception of API
and non-snapset file systems that are bind mounted from the host). The mounted
snapshot set can then be browsed interactively or used as a target for
commands.

Automatic Mount Management
--------------------------

All ``snapm`` operations that require access to a snapshot set's
contents will automatically mount the snapshot set if it is not
already mounted. When the operation completes, the snapshot set is
unmounted only if it was not already mounted before the operation
began.

This means that commands like ``snapset exec``, ``snapset shell``,
and ``snapset diff`` can be used without explicitly mounting first.
However, if you plan to perform multiple operations on the same
snapshot set, explicitly mounting it first with ``snapset mount``
is more efficient as it avoids repeated mount and unmount cycles:

.. code-block:: bash

   # Without explicit mount: each command mounts and unmounts
   snapm snapset exec backup rpm -qa > /tmp/backup-packages.txt
   snapm snapset exec backup grep root /etc/passwd
   snapm snapset exec backup cat /etc/hostname

   # With explicit mount: mount once, operate, unmount once
   snapm snapset mount backup
   snapm snapset exec backup rpm -qa > /tmp/backup-packages.txt
   snapm snapset exec backup grep root /etc/passwd
   snapm snapset exec backup cat /etc/hostname
   snapm snapset umount backup

The second approach is significantly faster when performing several
operations, as the mount and unmount overhead is incurred only once.

Use Cases
---------

The Mount Manager enables several common administrative tasks:

* **File Recovery**: Browse and copy individual files from a previous
  system state without reverting the entire snapshot set.

* **Configuration Inspection**: Examine configuration files from a
  snapshot to investigate the state at a particular point in time.

* **Package Inspection**: Query the package database within a snapshot
  to determine what was installed at a particular point in time.

* **Log Analysis**: Access log files from a snapshot to investigate
  issues that occurred before the current system state.

* **Script Execution**: Run maintenance or diagnostic scripts against
  the snapshotted file system state.

Interactive Access
------------------

The ``snapset shell`` command provides an interactive shell session
within the mounted snapshot set environment. This is the simplest way
to explore a snapshot set interactively:

.. code-block:: bash

   snapm snapset shell backup

This mounts the snapshot set (if not already mounted), changes the
root to the mounted snapshot, and starts a shell. When the shell
exits, the snapshot set is unmounted if it was not already mounted
before the command was run. The shell defaults to ``/bin/bash`` if the
``SHELL`` environment variable is unset; otherwise the executable specified in
``SHELL`` will be executed. The chosen shell must exist in the corresponding
snapshot set.

Command Execution
-----------------

For scripted or non-interactive use, the ``snapset exec`` command
allows running a specific command within the snapshot set environment:

.. code-block:: bash

   snapm snapset exec backup rpm -qa

The command and any arguments following the snapshot set name are
passed through to the execution environment.

Direct Mount Point Access
-------------------------

When a snapshot set is explicitly mounted, its contents can also be
accessed directly through the mount point at
``/run/snapm/mounts/<snapset_name>`` using normal file system
operations:

.. code-block:: bash

   # Mount the snapshot set
   snapm snapset mount backup

   # Access contents directly via the mount point
   ls /run/snapm/mounts/backup/etc
   cp /run/snapm/mounts/backup/etc/nginx/nginx.conf /tmp/
   grep -r "error" /run/snapm/mounts/backup/var/log/

   # Unmount when finished
   snapm snapset umount backup

The mount status of a snapshot set is visible in the ``snapset show``
and ``snapset list`` output via the ``Mounted`` field.

Difference Engine
=================

The Difference Engine is a core facility in ``snapm`` that allows for detailed
comparisons between two snapshot sets, or between a snapshot set and the
currently running system.

How it Works
------------

The engine operates by recursively walking the file system trees of both the
source and target. It performs a multi-stage analysis to identify
changes:

* **Entry Detection**: Identifies files, directories, and symlinks that have
  been added, removed, or modified.
* **Move Detection**: By matching content hashes, the engine can identify
  files that have been renamed or moved between directories rather than
  simply being deleted and recreated.
* **Metadata Analysis**: Detects changes in file permissions (mode),
  ownership (UID/GID), and extended attributes (xattrs).
* **Content Comparison**: Performs deep content comparisons for regular
  files, including generating standard unified diffs for text files.
* **Start Path Control**: By default difference engine operations proceed from
  the root directory of snapshot sets (or the system root file system). This
  can be overridden by specifying one or more start paths for the comparison.
  This allows the user to focus the operation on areas of interest and lowers
  run time and memory consumption.

Output Formats and Use Cases
----------------------------

The Difference Engine supports several output formats via the ``snapset diff``
``--output-format`` argument, each suited to different tasks:

* **paths**: Useful for scripting, allowing you to pipe a list of changed
  files to other tools like ``tar`` or ``rsync``.
* **full**: Provides a detailed report including old and new file attributes
  (permissions, ownership, size, modification time) for every change.
* **short**: A concise summary listing the change status code and path for
  each modified file.
* **json**: Outputs the complete difference data as a JSON object, suitable
  for parsing by external tools and automation scripts.
* **diff**: Generates a standard unified diff of content changes for modified
  files. This format is useful for generating patches or detailed content
  review.
* **summary**: Brief summary of the count of different change types. Change
  types are colorized using the same coding as ``tree`` output.
* **tree**: (Default) Displays changes in a hierarchical tree structure,
  similar to the standard ``tree`` command but annotated with change status.

In addition differences can be reported in a tabular format using the standard
``snapm`` reporting mechanism using the ``snapset diffreport`` command. The
available ``diffreport`` report output fields are:

* **path**: Difference path (string)
* **type**:  Difference type (string)
* **size_delta**: Diff size change (size)
* **content**: Content changed (string)
* **metadata**: Metadata changed (string)
* **mode_old**: Old path mode (string)
* **mode_new**: New path mode (string)
* **category**: File category (string)
* **movedfrom**: Move source (string)
* **movedto**: Move destination (string)
* **diffsummary**: Summary of content differences (string)
* **hash_old**: Old file content hash (string)
* **hash_new**: New file content hash (string)
* **filetype**: File type (string)
* **mtime_old**: Old modification time (time)
* **mtime_new**: New modification time (time)

Comparison Modes
----------------

You can fine-tune the comparison using various flags:

* **Content-Only** (``-c``): Ignores all metadata changes and only reports
  files where the actual data has changed.
* **Ignore Options**: Use ``--ignore-timestamps``, ``--ignore-permissions``,
  or ``--ignore-ownership`` to filter out "noise" during comparisons.

Performance and the Diff Cache
------------------------------

Walking large file systems and computing content hashes can be
resource-intensive. To improve performance, ``snapm`` implements a
**diffcache**.

* **How it works**: When a comparison is performed, the results are
  compressed and stored in ``/var/cache/snapm/diffcache``.
* **Cache Reuse**: Subsequent ``diff`` or ``diffreport`` commands between
  the same two sets will load the results from the cache instead of
  re-scanning the file systems.
* **Expiry**: By default, cache entries expire after **15 minutes**.
  This ensures that results remain fresh if the running system (``.``) is
  one of the comparison targets. Use ``--cache-expires=EXPIRES_SECS`` to
  override the default value (900s).
* **Manual Control**: You can override the expiry or disable the cache
  entirely using the ``--cache-mode={auto,never,always}`` and
  ``--cache-expires=EXPIRES_SECS`` options. These options are mutually
  exclusive.

By default a ``diff`` or ``diffreport`` command will start at the root mount
of the respective snapshot set (or the system root file system). This can be
costly both in terms of time and memory consumption. The comparison can be
narrowed to specific directories of interest by specifying one or more start
paths using ``-s|--start-path PATH``. If no start path is given the tools
will warn about the potential resource use. This warning can be suppressed if
desired using ``--quiet``.

Command Reference
=================

The ``snapm`` Command
---------------------

The ``snapm`` command provides the CLI interface to the snapshot
manager. It is able to create, delete, and display snapshots and
snapshot sets and provides reports listing the snapshots, snapshot
sets, and schedules available on the system.

Each snapm command operates on a particular object type: a snapshot set,
an individual snapshot, or a schedule.

.. code-block:: bash

   snapm snapset <command> <options>  # snapset command

.. code-block:: bash

   snapm snapshot <command> <options> # snapshot command

Snapset Commands
================

The ``snapset`` subcommand is used to create, delete, rename, activate,
deactivate, list and display snapshot sets.

snapset create
--------------

Create a new snapshot set with the provided name and list of sources
(mount point or block device paths):

.. code-block:: bash

   snapm snapset create [-b|--bootable] [-r|--revert] [--size-policy policy] <name> <source> [<source> ...]

Per-source path size policies are specified by adding a ':' and the
required policy to the corresponding mount point path, for example:

.. code-block:: bash

   snapm snapset create backup /:2G /var:1G /home

.. code-block:: bash

   snapm snapset create backup /:25%FREE /var:25%FREE /home

.. code-block:: bash

   snapm snapset create backup /:100%USED /var:100%USED /home

.. code-block:: bash

   snapm snapset create backup /:100%SIZE /var:100%SIZE /home

If no size policy is specified the default is ``200%USED`` for mounted
file systems and 25%SIZE for unmounted block devices. To ensure a volume
can be completely overwritten specify ``100%SIZE``. This requires more
storage capacity but avoids the possibility of the snapshot running out
of space.

A default size policy for all source paths that do not specify an
explicit per-path policy can be set with the ``--size-policy`` argument:

.. code-block:: bash

   snapm snapset create backup --size-policy 100%SIZE / /home /var

On success the ``snapm snapset create`` command displays the newly
created snapshot set on stdout:

.. code-block:: bash

   snapm snapset create -br --size-policy 100%USED backup / /home /var
   SnapsetName:      backup
   Sources:          /, /home, /var
   NrSnapshots:      3
   Time:             2024-12-05 19:10:44
   UUID:             d3f5e3cd-a383-5dba-b597-9134a2c426e9
   Status:           Active
   Categories:       daily, hourly
   Autoactivate:     yes
   Bootable:         yes
   OriginMounted:    yes
   Mounted:          no
   BootEntries:
     SnapshotEntry:  f574a20
     RevertEntry:    f428f9f

Autoindex for recurring snapshot sets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``--autoindex`` argument allows creating a recurring snapshot set
with a common basename and unique index (a non-negative integer). This
can be used to take regular snapshots with a common name:

.. code-block:: bash

   snapm snapset create hourly --autoindex /:25%SIZE /var:25%SIZE
   SnapsetName:      hourly.3
   Sources:          /, /var
   NrSnapshots:      2
   Time:             2025-03-26 14:17:18
   UUID:             ae082452-7995-5316-ac65-388eadd9879c
   Status:           Active
   Categories:       daily, hourly
   Autoactivate:     yes
   OriginMounted:    yes
   Mounted:          no
   Bootable:         no

The basename and index values are available via the ``snapset list``
report:

.. code-block:: bash

   snapm snapset list -o+basename,index
   SnapsetName  Time                 NrSnapshots Status  Sources  Mounted  Basename     Index
   backup       2025-03-25 18:12:54            2 Invalid /, /var  no       backup           -
   hourly.0     2025-03-25 19:40:39            2 Invalid /, /var  yes      hourly           0
   hourly.1     2025-03-26 14:17:11            2 Active  /, /var  no       hourly           1
   hourly.2     2025-03-26 14:17:15            2 Active  /, /var  no       hourly           2
   hourly.3     2025-03-26 14:17:18            2 Active  /, /var  no       hourly           3

snapset delete
--------------

Delete an existing snapset by name or uuid.

.. code-block:: bash

   snapm snapset delete <name|uuid>

snapset diff
------------

Compare two snapshot sets, or a snapshot set and the running system.

.. code-block:: bash

   snapm snapset diff [options] <from> <to>

The ``from`` and ``to`` arguments specify the snapshot sets to compare.
The special value ``.`` may be used to refer to the running system (the
root file system).

The command detects added, removed, and modified files. File content
comparisons can be performed to generate standard unified diffs.

Supported output formats (``-o``/``--output-format``) include:

* **paths** - A simple list of changed paths
* **full** - Detailed change information
* **short** - Abbreviated change summary
* **json** - JSON formatted change data
* **diff** - Standard unified diff of file content
* **summary** - Brief summary of the count of different change types
* **tree** - A hierarchical tree view of changes (default)

The ``-o``/``--output-format`` option may be given multiple times to request
different output formats for the same run.

The ``diff`` and ``summary`` formats also accept an optional ``--stat``
option to enable ``diffstat`` style change summaries.

The ``json`` output format supports ``--pretty`` to enable pretty-printing
the JSON output.

Compare the snapshot set "backup" to the running system using a tree view:

.. code-block:: bash

   snapm snapset diff --output-format tree backup .

Generate a unified diff of content changes between two snapshot sets,
ignoring file modification times to focus only on content:

.. code-block:: bash

   snapm snapset diff --output-format diff --ignore-timestamps weekly.0 weekly.1

Options are available to control the comparison:

* ``--start-path PATH`` / ``-s PATH``: Begin comparison at ``PATH``
  (may be specified zero or more times)
* ``--content-only`` / ``-c``: Only check for file content changes
* ``--ignore-timestamps`` / ``-t``: Ignore modification times
* ``--ignore-permissions`` / ``-p``: Ignore permission changes
* ``--ignore-ownership`` / ``-w``: Ignore ownership changes
* ``--include-pattern`` / ``--exclude-pattern``: Filter paths using glob patterns

The ``PATH`` value given to ``--start-path PATH`` / ``-s PATH`` is evaluated
relative to the root directory of the snapshot set (or system root file system
when using ``.``).

Output Formats
~~~~~~~~~~~~~~

The ``--output-format`` argument controls the presentation of differences.
Available formats are:

* **paths**: Lists modified paths, one per line. Useful for piping to other
  tools or scripts.
* **full**: Provides a detailed report including old and new file attributes
  (permissions, ownership, size, modification time) for every change.
* **short**: A concise summary listing the change status code and path for
  each modified file.
* **json**: Outputs the complete difference data as a JSON object, suitable
  for parsing by external tools and automation scripts.
* **diff**: Generates a standard unified diff of content changes for modified
  files. This format is useful for generating patches or detailed content
  review.
* **summary**: Brief summary of the count of different change types. Change
  types are colorized using the same coding as ``tree`` output.
* **tree**: (Default) Displays changes in a hierarchical tree structure,
  similar to the standard ``tree`` command but annotated with change status.

The default if no ``--output-format`` option is given is ``tree``.

Tree Output Legend
~~~~~~~~~~~~~~~~~~

The **tree** output format uses specific prefixes and colors (if enabled)
to indicate the type of change:

* ``[+]`` **Path added** (green): The file or directory is present in the
  target but not the source.
* ``[-]`` **Path removed** (red): The file or directory is present in the
  source but not the target.
* ``[*]`` **Path modified** (yellow): The file content or attributes have
  changed.
* ``[<]`` **Path moved from** (cyan): The source path of a renamed or moved
  file.
* ``[>]`` **Path moved to** (cyan): The destination path of a renamed or
  moved file.
* ``[!]`` **Path file type changed** (blue): The file type (regular file,
  symlink, directory, etc.) has changed between source and target.

snapset diffreport
------------------

Generate a tabular report of differences between snapshot sets.

.. code-block:: bash

   snapm snapset diffreport [options] <from> <to>

This command performs the same comparison logic as ``snapset diff`` but
formats the output as a column-based report. It accepts standard
reporting arguments including ``--options``, ``--sort``, ``--noheadings``,
and ``--json``.

.. code-block:: bash

   snapm snapset diffreport --json backup current

To list specific fields describing the changes:

.. code-block:: bash

   snapm snapset diffreport -opath,state,size_diff old_snap new_snap

snapset rename
--------------

Rename an existing snapset.

.. code-block:: bash

   snapm snapset rename <old_name> <new_name>

snapset revert
--------------

Revert an existing snapset, re-setting the content of the origin volumes
to the state they were in at the time the snapset was created. The
snapset to be reverted may be specified either by its name or uuid.

.. code-block:: bash

   snapm snapset revert <name|uuid>

If the origins of the snapshot set are in use at the time of the revert
the operation is deferred until the next time the snapshot set is
activated (for example during a reboot). If a revert boot entry was
created for the snapshot set the ``revert`` command will suggest booting
into it to continue:

.. code-block:: bash

   snapm snapset revert upgrade
   WARNING - Snapshot set upgrade origin is in use: reboot required to complete revert
   Boot into 'Revert upgrade 2024-06-10 15:25:15 (6.8.9-300.fc40.x86_64)' to continue

snapset resize
--------------

Resize the members in an existing snapset, applying a new size policy to
specified sources or applying a new default size policy to all snapshots
within a snapset.

Resize the ``/var`` member of the snapshot set named upgrade to
100%SIZE:

.. code-block:: bash

   snapm snapset resize upgrade /var:100%SIZE

Resize each member of the snapshot set named backup to the 200%USED size
policy:

.. code-block:: bash

   snapm snapset resize backup --size-policy 200%USED

snapset split
-------------

Split snapshots from an existing snapshot set into a new snapshot set.

Split the snapshot set named 'name' into a new snapshot set named
'new_name'. Each listed source from 'name' is split into the new
snapshot set. Sources that are not listed on the command line remain
part of the original snapshot set. It is an error to split all sources
from a snapshot set: in this case use 'snapm snapset rename' instead.

To split the source "/home" from the existing snapshot set "upgrade"
into a new snapshot set named "noupgrade":

.. code-block:: bash

   snapm snapset split upgrade noupgrade /home
   SnapsetName:      noupgrade
   Sources:          /home
   NrSnapshots:      1
   Time:             2025-03-31 20:21:29
   UUID:             30e69b86-5c48-5e5d-be1a-bf3d63aef8f7
   Status:           Inactive
   Categories:       daily, hourly
   Autoactivate:     no
   Bootable:         no

snapset activate
----------------

Activate the members of an existing snapset, or all snapsets if no name
or uuid argument is given.

.. code-block:: bash

   snapm snapset activate [<name|uuid>]

snapset deactivate
------------------

Deactivate the members of an existing snapset, or all snapsets if no
name or uuid argument is given.

.. code-block:: bash

   snapm snapset deactivate [<name|uuid>]

snapset autoactivate
--------------------

Enable or disable autoactivation for the snapshots in a snapshot set.

.. code-block:: bash

   snapm snapset autoactivate [--yes|--no] [<name|uuid>]

snapset list
------------

List available snapsets matching selection criteria.

.. code-block:: bash

   snapm snapset list [<name|uuid>]

By default the information is presented as a tabular report with column
headings indicating the meaning of each value. The default column
selection includes the SnapsetName, Time, NrSnapshots, Status, and
Sources fields:

.. code-block:: bash

   snapm snapset list
   SnapsetName  Time                 NrSnapshots Status  Sources         Mounted
   backup       2024-12-05 19:14:03            3 Active  /, /home, /var  no
   upgrade      2024-12-05 19:14:09            2 Active  /, /var         yes

Custom field specifications may be given with the ``-o``/``--options``
argument. To obtain a list of available fields run ``snapm snapset list
-ohelp``:

.. code-block:: bash

   snapm snapset list -ohelp
   Snapshot set Fields
   -------------------
     name           - Snapshot set name [str]
     uuid           - Snapshot set UUID [uuid]
     basename       - Snapshot set basename [str]
     index          - Snapshot set index [idx]
     timestamp      - Snapshot set creation time as a UNIX epoch value [num]
     time           - Snapshot set creation time [time]
     nr_snapshots   - Number of snapshots [num]
     sources        - Snapshot set sources [strlist]
     mountpoints    - Snapshot set mount points [strlist]
     devices        - Snapshot set devices [strlist]
     status         - Snapshot set status [str]
     autoactivate   - Autoactivation status [str]
     mounted        - SnapshotSet mount status [str]
     origin_mounted - SnapshotSet origin mount status [str]
     mount_root     - SnapshotSet mount root directory path [str]
     bootable       - Configured for snapshot boot [str]
     bootentry      - Snapshot set boot entry [sha]
     revertentry    - Snapshot set revert boot entry [sha]
     categories     - Snapshot set timeline categories [strlist]

To specify custom fields pass a comma separated list to ``-o``:

.. code-block:: bash

   snapm snapset list -oname,time
   SnapsetName  Time
   backup       2024-12-05 19:14:03
   upgrade      2024-12-05 19:14:09

To add fields to the default field set prefix the list of fields with
the ``+`` character:

.. code-block:: bash

   snapm snapset list -o+bootentry,revertentry
   SnapsetName  Time                 NrSnapshots Status  Sources        SnapshotEntry RevertEntry
   backup       2024-12-05 19:14:03            3 Active  /, /home, /var 41573a414f9d5 1cc5bc59c9b90
   upgrade      2024-12-05 19:14:09            2 Active  /, /var        a60dab4d3fb36 4ce6b27f16f30

The report can also be produced in JSON notation, suitable for parsing
by other tools using the ``--json`` argument:

.. code-block:: bash

   snapm snapset list --json
   {
       "Snapsets": [
           {
               "snapset_name": "backup",
               "snapset_time": "2024-12-05 19:14:03",
               "snapset_nr_snapshots": 3,
               "snapset_status": "Active",
               "snapset_sources": [
                   "/",
                   "/home",
                   "/var"
               ],
               "snapset_mounted": false
           },
           {
               "snapset_name": "upgrade",
               "snapset_time": "2024-12-05 19:14:09",
               "snapset_nr_snapshots": 2,
               "snapset_status": "Active",
               "snapset_sources": [
                   "/",
                   "/var"
               ],
               "snapset_mounted": true
           }
       ]
   }

JSON reports use the full report field name (including the ``snapset_``,
``snapshot_``, ``schedule_``, or ``plugin_`` prefix). Fields and sort
order are specified with ``--options`` and ``--sort`` as usual.

For further report formatting options refer to the ``snapm(8)`` manual
page.

snapset show
------------

Display available snapsets matching selection criteria.

.. code-block:: bash

   snapm snapset show [<name|uuid>]

By default the output is formatted in the same way as the output of the
``snapm snapset create`` command:

.. code-block:: bash

   snapm snapset show upgrade
   SnapsetName:      upgrade
   Sources:          /, /var
   NrSnapshots:      2
   Time:             2024-12-05 19:14:09
   UUID:             87c6df8f-bd8c-5c9d-b081-4f6d6068cc07
   Status:           Active
   Categories:       monthly, weekly, daily, hourly
   Autoactivate:     yes
   OriginMounted:    yes
   Mounted:          no
   Bootable:         yes
   BootEntries:
     SnapshotEntry:  a60dab4
     RevertEntry:    4ce6b27

The individual snapshots making up each set are also displayed if
``--members`` is used:

.. code-block:: bash

   snapm snapset show --members
   SnapsetName:      upgrade
   Sources:          /, /var
   NrSnapshots:      2
   Time:             2024-12-05 19:19:30
   UUID:             f0a46cde-9eed-5335-b239-66ed53e5b503
   Status:           Active
   Categories:       daily, hourly
   Autoactivate:     yes
   OriginMounted:    yes
   Mounted:          no
   Bootable:         yes
   BootEntries:
     SnapshotEntry:  dfce8d8
     RevertEntry:    fc414b0
   Snapshots:
       Name:           fedora/root-snapset_upgrade_1733426370_-
       SnapsetName:    upgrade
       Origin:         /dev/fedora/root
       Time:           2024-12-05 19:19:30
       Source:         /
       MountPoint:     /
       Provider:       lvm2-cow
       UUID:           7566dde3-96f4-5288-8b15-18be7c520327
       Status:         Active
       Size:           8.8GiB
       Free:           8.8GiB
       Autoactivate:   yes
       DevicePath:     /dev/fedora/root-snapset_upgrade_1733426370_-
       VolumeGroup:    fedora
       LogicalVolume:  root-snapset_upgrade_1733426370_-

       Name:           fedora/var-snapset_upgrade_1733426370_-var
       SnapsetName:    upgrade
       Origin:         /dev/fedora/var
       Time:           2024-12-05 19:19:30
       Source:         /var
       MountPoint:     /var
       Provider:       lvm2-cow
       UUID:           22674f3e-f5c4-5632-9add-2df51985679e
       Status:         Active
       Size:           6.4GiB
       Free:           6.4GiB
       Autoactivate:   yes
       DevicePath:     /dev/fedora/var-snapset_upgrade_1733426370_-var
       VolumeGroup:    fedora
       LogicalVolume:  var-snapset_upgrade_1733426370_-var

The output is also available in JSON notation using the ``--json``
argument:

.. code-block:: bash

  snapm snapset show --json before-upgrade
  [
      {
          "SnapsetName": "before-upgrade",
          "Sources": [
              "/",
              "/var"
          ],
          "MountPoints": [
              "/",
              "/var"
          ],
          "Devices": [],
          "NrSnapshots": 2,
          "Timestamp": 1756555124,
          "Time": "2025-08-30 12:58:44",
          "UUID": "87e31113-75a5-5eb6-b016-762639a2c7ed",
          "Status": "Active",
          "Categories": [
              "yearly",
              "monthly",
              "daily",
              "hourly"
          ],
          "Autoactivate": true,
          "OriginMounted": true,
          "Mounted": false,
          "Bootable": true,
          "BootEntries": {
              "SnapshotEntry": "7c56dc0",
              "RevertEntry": "457f733"
          }
      }
  ]

The ``show --json``  command uses the normal ``show`` output property names as
the JSON keys. A ``BootEntries`` object will be added if either boot or revert
entries are configured for the snapshot set.

snapset mount
-------------

Mount the members of an existing snapshot set.

.. code-block:: bash

   snapm snapset mount <snapset_name>

The snapshot set's root volume is mounted at
``/run/snapm/mounts/<snapset_name>``. Additional snapshot volumes are
mounted from their corresponding snapshot devices to the appropriate
paths within this hierarchy. API and runtime file systems, along with
any non-snapset file systems such as ``/boot``, are bind-mounted from
the host system.

.. code-block:: bash

   snapm snapset mount backup
   ls /run/snapm/mounts/backup/

Once mounted, the snapshot set contents can be accessed directly
through the mount point, or via ``snapset exec`` and ``snapset shell``
commands, until explicitly unmounted with ``snapset umount``.

snapset umount
--------------

Unmount the members of an existing snapshot set.

.. code-block:: bash

   snapm snapset umount <snapset_name>

This reverses the mount operations performed by ``snapset mount``,
unmounting all volumes from ``/run/snapm/mounts/<snapset_name>``.

.. code-block:: bash

   snapm snapset umount backup

snapset exec
------------

Execute a command within a snapshot set environment.

.. code-block:: bash

   snapm snapset exec <snapset_name> <command> [args...]

If the snapshot set is not already mounted, it is mounted
automatically. The specified command is executed with the snapshot
set as its root file system. If the snapshot set was not mounted
before the command, it is unmounted afterward. Arguments following
the command are passed through verbatim.

Query the package list from a snapshot:

.. code-block:: bash

   snapm snapset exec backup rpm -qa

Search for a pattern in configuration files:

.. code-block:: bash

   snapm snapset exec backup grep -r "Listen" /etc/httpd/

Check disk usage within the snapshot:

.. code-block:: bash

   snapm snapset exec backup df -h

snapset shell
-------------

Start an interactive shell within a snapshot set environment.

.. code-block:: bash

   snapm snapset shell <snapset_name>

If the snapshot set is not already mounted, it is mounted
automatically. An interactive shell session is started with the
snapshot set as its root file system. When the shell exits, the
snapshot set is unmounted if it was not mounted before the command
was run.

.. code-block:: bash

   snapm snapset shell backup
   # Now operating within the 'backup' snapshot environment
   # Use 'exit' to return to the normal system

This provides a convenient way to interactively explore a snapshot
set's contents, recover files, or run multiple commands without
needing to prefix each with ``snapset exec``.

Snapshot Commands
=================

The ``snapshot`` command is used to manipulate, list, and display
snapshots.

snapshot activate
-----------------

Activate individual snapshots matching selection criteria.

.. code-block:: bash

   snapm snapshot activate [-N name] [-U uuid] [<name|uuid>]

snapshot deactivate
-------------------

Deactivate individual snapshots matching selection criteria.

.. code-block:: bash

   snapm snapshot deactivate [-N name] [-U uuid] [<name|uuid>]

snapshot autoactivate
---------------------

Enable or disable autoactivation for individual snapshots matching
selection criteria.

.. code-block:: bash

   snapm snapshot autoactivate [--yes|--no] [-N name] [-U uuid] [<name|uuid>]

snapshot list
-------------

List available snapshots matching selection criteria.

.. code-block:: bash

   snapm snapshot list [<name|uuid>]

By default the information is presented as a tabular report with column
headings indicating the meaning of each value. The default column
selection includes the SnapsetName, Name, Origin, Source, Status,
Size, Free, Autoactivate, and Provider fields:

.. code-block:: bash

   snapm snapshot list
   SnapsetName  Name                                         Origin           Source  Status  Size   Free   Autoactivate Provider
   upgrade      fedora/root-snapset_upgrade_1733426499_-     /dev/fedora/root /       Active  8.8GiB 8.8GiB yes          lvm2-cow
   upgrade      fedora/var-snapset_upgrade_1733426499_-var   /dev/fedora/var  /var    Active  6.4GiB 6.4GiB yes          lvm2-cow
   upgrade      fedora/home-snapset_upgrade_1733426499_-home /dev/fedora/home /home   Active  1.0GiB 1.9GiB yes          lvm2-thin

.. note::
   For thin‑provisioned snapshots (lvm2‑thin, Stratis), “Free” reflects
   thin‑pool free space and may exceed “Size”

snapshot show
-------------

Display available snapshots matching selection criteria.

.. code-block:: bash

   snapm snapshot show [<name|uuid>]

Plugin Commands
===============

The ``plugin`` command is used to display information on the available
snapshot provider plugins.

Plugin Priority
---------------

Snapshot manager uses a priority system to select which plugin should handle
snapshot operations when multiple plugins are capable of snapshotting a given
source. Higher priority values are preferred over lower values.

Each plugin has a default priority based on its snapshot implementation:

* **lvm2-cow** (LVM2 copy-on-write): Priority 10
* **lvm2-thin** (LVM2 thin provisioning): Priority 15
* **stratis** (Stratis storage): Priority 20

plugin list
-----------

The ``plugin list`` command displays information about each plugin, including
the configured priority values:

.. code-block:: bash

   snapm plugin list
   PluginName PluginVersion PluginType       Priority
   lvm2-cow   0.2.0         Lvm2CowSnapshot        10
   lvm2-thin  0.2.0         Lvm2ThinSnapshot       15
   stratis    0.2.0         StratisSnapshot        20

Overriding Plugin Priority
~~~~~~~~~~~~~~~~~~~~~~~~~~

The default plugin priorities can be overridden using configuration files in
``/etc/snapm/plugins.d/``. This allows administrators to prefer one snapshot
implementation over another when multiple backends are available, overriding
the built-in default priority levels.

For example, to prefer LVM2 copy-on-write over thin provisioning snapshots,
edit ``/etc/snapm/plugins.d/lvm2-cow.conf``:

.. code-block:: ini

   [Priority]
   PluginPriority = 18

This sets the lvm2-cow plugin priority to 18, making it preferred over
lvm2-thin (default priority 15). After creating or modifying plugin
configuration files, verify the changes:

.. code-block:: bash

   snapm plugin list
   PluginName PluginVersion PluginType       Priority
   lvm2-cow   0.2.0         Lvm2CowSnapshot        18
   lvm2-thin  0.2.0         Lvm2ThinSnapshot       15
   stratis    0.2.0         StratisSnapshot        20

Plugin priority values must be positive integers. Setting a priority to 0 or
a negative value will generate a warning message and cause the plugin to use
its default priority.

Plugin Selection Behavior
~~~~~~~~~~~~~~~~~~~~~~~~~

When creating snapshots, ``snapm`` queries all available plugins to determine
which can handle each source. If multiple plugins are capable, the plugin with
the highest priority value is selected.

Priority-based selection applies when:

* Creating new snapshot sets with ``snapset create``
* Configuring or creating scheduled snapshots with ``schedule create`` or
  ``snapset create-scheduled``
* Sources that could use multiple snapshot backends (e.g., LVM2 volumes that
  support both copy-on-write and thin provisioning)

Priority does not affect:

* Existing snapshots (which always use their original plugin)
* Sources that can only be handled by a single plugin type

Plugin List Report Fields
--------------------------

The ``plugin list`` command supports the following report fields:

Plugin fields:

.. code-block:: bash

   snapm plugin list -ohelp
   Plugin Fields
   -------------
     name         - Name of the plugin [str]
     version      - Version of the plugin [str]
     type         - The snapshot type created by this plugin [str]
     priority     - The current priority assigned to this plugin [num]

The default field selection includes name, version, type, and priority.

Schedule Commands
=================

The ``schedule`` command is used to create, display and manage schedules
for automatic snapshot set creation.

schedule create
---------------

Create a new schedule with the provided name and list of sources (mount
point or block device paths):

.. code-block:: bash

   snapm schedule create [-b|--bootable] [-r|--revert] [--size-policy policy] [-p|--policy-type policy_type] [--keep-count count] [--keep-years years] [--keep-months months] [--keep-weeks weeks] [--keep-days days] [--keep-yearly yearly] [--keep-quarterly quarterly] [--keep-monthly monthly] [--keep-weekly weekly] [--keep-daily daily] [--keep-hourly hourly] --calendarspec calendarspec <name> source...

.. code-block:: bash

   snapm schedule create --policy-type count --keep-count 2 --bootable --revert --size-policy 25%SIZE --calendarspec hourly hourly / /var
   Name: hourly
   SourceSpecs: /, /var
   DefaultSizePolicy: 25%SIZE
   Calendarspec: hourly
   Boot: yes
   Revert: yes
   GcPolicy:
       Name: hourly
       Type: Count
       Params: keep_count=2
   Enabled: yes
   Running: yes

schedule delete
---------------

Delete an existing schedule by name:

.. code-block:: bash

   snapm schedule delete <name>

schedule edit
-------------

Modify an existing schedule by name:

.. code-block:: bash

   snapm schedule edit [-b|--bootable] [-r|--revert] [--size-policy policy] [-p|--policy-type policy_type] [--keep-count count] [--keep-years years] [--keep-months months] [--keep-weeks weeks] [--keep-days days] [--keep-yearly yearly] [--keep-quarterly quarterly] [--keep-monthly monthly] [--keep-weekly weekly] [--keep-daily daily] [--keep-hourly hourly] --calendarspec calendarspec <name> [source...]

schedule enable
---------------

Enable an existing schedule by name:

.. code-block:: bash

   snapm schedule enable <name>

schedule disable
----------------

Disable an existing schedule by name:

.. code-block:: bash

   snapm schedule disable <name>

schedule list
-------------

List configured schedules:

.. code-block:: bash

   snapm schedule list [--nameprefixes] [--noheadings] [--options fields] [--sort fields] [--rows|--json] [--separator separator]

.. code-block:: bash

   snapm schedule list
   ScheduleName ScheduleSources         SizePolicy OnCalendar     Enabled
   custom       /, /home:100%SIZE, /var 50%SIZE    *-*-1 01:00:00 yes
   monthly      /:25%SIZE, /var:25%SIZE            monthly        no
   hourly       /, /var                 25%SIZE    hourly         yes

schedule show
-------------

Display configured schedule:

.. code-block:: bash

   snapm schedule show <name>

schedule gc
-----------

Run configured garbage collection policy for schedule:

.. code-block:: bash

   snapm schedule gc <name>

Reporting Commands
==================

The ``snapm snapset list``, ``snapm snapshot list``, ``snapm plugin
list``, and ``snapm schedule list`` commands generate a tabular report
as output. To control the list of displayed fields use the
``-o``/``--options FIELDS`` argument:

.. code-block:: bash

   snapm snapset list -oname,sources
   SnapsetName  Sources
   backup       /, /home, /var
   userdata     /data, /home

To add extra fields to the default selection, prefix the field list with
the ``+`` character:

.. code-block:: bash

   snapm snapset list -o+uuid
   SnapsetName  Time                 NrSnapshots Status   Sources        UUID
   backup       2024-12-05 19:26:28            3 Active   /, /home, /var 53514020-e88d-5f53-bf09-42c6ab6e325d
   userdata     2024-12-05 19:26:45            2 Inactive /data, /home   e8d58051-7a94-5802-8328-54661ab1a70f

For example, to display timeline categories for each snapshot set, add the
"categories" field to the field list with the ``+`` character:

.. code-block:: bash

   snapm snapset list -o+categories
   SnapsetName    Time                 NrSnapshots Status  Sources  Mounted Categories
   before-upgrade 2025-12-12 12:25:36            2 Active  /, /var  no      daily, hourly, monthly, yearly
   just-now       2025-12-20 18:47:12            2 Invalid /, /var  no      daily, hourly
   today          2026-01-02 20:53:21            2 Invalid /, /var  no      daily, hourly, monthly, quarterly, yearly

Or, for a more compact display, specify the whole field list with ``-o``:

.. code-block:: bash

    snapm snapset list -oname,time,categories
    SnapsetName    Time                 Categories
    before-upgrade 2025-12-12 12:25:36  daily, hourly, monthly, yearly
    just-now       2025-12-20 18:47:12  daily, hourly
    today          2026-01-02 20:53:21  daily, hourly, monthly, quarterly, yearly

To display the available fields for either report use the field name
``help``:

.. code-block:: bash

  snapm snapset list -ohelp
  Snapshot set Fields
  -------------------
    name           - Snapshot set name [str]
    uuid           - Snapshot set UUID [uuid]
    basename       - Snapshot set basename [str]
    index          - Snapshot set index [idx]
    timestamp      - Snapshot set creation time as a UNIX epoch value [num]
    time           - Snapshot set creation time [time]
    nr_snapshots   - Number of snapshots [num]
    sources        - Snapshot set sources [strlist]
    mountpoints    - Snapshot set mount points [strlist]
    devices        - Snapshot set devices [strlist]
    status         - Snapshot set status [str]
    autoactivate   - Autoactivation status [str]
    mounted        - SnapshotSet mount status [str]
    origin_mounted - SnapshotSet origin mount status [str]
    mount_root     - SnapshotSet mount root directory path [str]
    bootable       - Configured for snapshot boot [str]
    bootentry      - Snapshot set boot entry [sha]
    revertentry    - Snapshot set revert boot entry [sha]
    categories     - Snapshot set timeline categories [strlist]

JSON Output
-----------

All reports can optionally be formatted as JSON for parsing by other
tools using the ``--json`` argument:

.. code-block:: bash

    snapm snapset list --json
    {
        "Snapsets": [
            {
                "snapset_name": "backup",
                "snapset_time": "2025-09-09 17:12:21",
                "snapset_nr_snapshots": 3,
                "snapset_status": "Inactive",
                "snapset_sources": [
                    "/",
                    "/home",
                    "/var"
                ],
                "snapset_mounted": false
            },
            {
                "snapset_name": "before-upgrade",
                "snapset_time": "2025-09-08 18:43:57",
                "snapset_nr_snapshots": 2,
                "snapset_status": "Active",
                "snapset_sources": [
                    "/",
                    "/var"
                ],
                "snapset_mounted": true
            }
        ]
    }

The ``create`` and ``show`` commands also support optional JSON output
using the ``--json`` argument. In this case the JSON schema maps the
normal ``show`` output property names to JSON keys:

.. code-block:: bash

    snapm snapset show --json before-upgrade
    [
        {
            "SnapsetName": "before-upgrade",
            "Sources": [
                "/",
                "/var"
            ],
            "MountPoints": [
                "/",
                "/var"
            ],
            "Devices": [],
            "NrSnapshots": 2,
            "Timestamp": 1757353437,
            "Time": "2025-09-08 18:43:57",
            "UUID": "6330328b-a9d0-5b41-ac96-53b371449965",
            "Status": "Active",
            "Categories": [
                "yearly",
                "monthly",
                "daily",
                "hourly"
            ],
            "Autoactivate": true,
            "OriginMounted": true,
            "Mounted": false,
            "Bootable": true,
            "BootEntries": {
                "SnapshotEntry": "66dc7ad",
                "RevertEntry": "6832c11"
            }
        }
    ]

Common Use Cases
================

System Updates and Rollback
---------------------------

Before performing system updates, create a bootable snapshot:

Create snapshot set before update:

.. code-block:: bash

   snapm snapset create --bootable --revert pre-update / /var

Perform system update:

.. code-block:: bash

   dnf update

If update causes issues, revert:

.. code-block:: bash

   snapm snapset revert pre-update

Or boot into snapshot from boot menu (select: "Snapshot pre-update
YYYY-MM-DD HH:MM:SS (version)"):

.. code-block:: bash

   reboot

Clean up when satisfied with update:

.. code-block:: bash

   snapm snapset delete pre-update

Development and Testing
-----------------------

Create development checkpoints for experimental work:

* Create checkpoint before major changes:

.. code-block:: bash

   snapm snapset create dev-checkpoint /home /var/lib/myapp

* Make experimental changes

* If changes don't work out, revert:

.. code-block:: bash

   snapm snapset revert dev-checkpoint

Data Protection
---------------

Regular data snapshots for important directories allow backups to be
captured from snapshots, lowering production impact by reducing backup
downtime.

Daily backup of user data:

.. code-block:: bash

   snapm snapset create daily-backup /home /var/lib/database

Weekly system snapshots:

.. code-block:: bash

   snapm snapset create weekly-system / /var --size-policy 50%SIZE

Automated Scheduling
--------------------

Set up automated snapshot schedules:

Hourly snapshots, keep 24:

.. code-block:: bash

   snapm schedule create --autoindex \
       --policy-type count \
       --keep-count 24 \
       --calendarspec hourly \
       hourly / /home

Daily snapshots, keep 7 days:

.. code-block:: bash

   snapm schedule create --autoindex \
       --policy-type age \
       --keep-days 7 \
       --calendarspec daily \
       daily / /home /var

Monthly bootable snapshots, keep 12:

.. code-block:: bash

   snapm schedule create --autoindex \
       --bootable --revert \
       --policy-type timeline \
       --keep-monthly 12 \
       --calendarspec monthly \
       monthly / /var

Configuration Management
------------------------

Snapshot configuration changes:

Before modifying system configuration:

.. code-block:: bash

   snapm snapset create config-backup /etc /var/lib/config

Make configuration changes:

.. code-block:: bash

   vim /etc/myapp/config.conf

Test changes...

If configuration breaks system, revert:

.. code-block:: bash

   snapm snapset revert config-backup

Best Practices
==============

Naming Conventions
------------------

Use consistent, descriptive naming:

Good examples:

* pre-kernel-update-6.5.0-snap
* before-database-migration
* dev-checkpoint-feature-x

Avoid generic names:

* backup
* test
* snapshot1

Size Policy Guidelines
----------------------

* For system volumes (``/``, ``/var``): Use ``100%SIZE`` for critical
  systems, ``200%USED`` for normal use
* For data volumes (``/home``): Use ``100%USED`` or ``50%SIZE``
  depending on change frequency
* For database volumes: Use ``300%USED`` or ``100%SIZE`` for heavy write
  workloads

Retention Policies
------------------

Plan retention based on your needs:

* **Hourly**: Keep 6-12 snapshots for short-term recovery
* **Daily**: Keep 3-7 snapshots for medium-term recovery
* **Weekly**: Keep 2-4 snapshots for long-term recovery
* **Monthly**: Keep 3-6 snapshots for archival purposes

Monitoring and Maintenance
--------------------------

Regular maintenance tasks:

Check snapshot space usage:

.. code-block:: bash

   snapm snapshot list -o name,size,free

Clean up old snapshots:

.. code-block:: bash

   snapm snapset delete old-snapshot-name

Review scheduled snapshots:

.. code-block:: bash

   snapm schedule list

Access Control
--------------

Snapm requires root privileges for most operations. Consider:

* Using sudo with specific command restrictions
* Creating wrapper scripts for common operations
* Implementing audit logging for snapshot operations

Data Sensitivity
----------------

Remember that snapshots contain complete copies of data:

* Consider encryption for sensitive data
* Implement proper cleanup procedures for temporary snapshots

Boot Security
-------------

Bootable snapshots can bypass some security measures:

* Bootable snapshot sets use the kernel present at the time of snapshot
  creation: older kernels may contain security vulnerabilities patched
  in newer builds.

  * Consider password protecting GRUB and enforcing physical access controls.

Integration Examples
====================

With Systemd Services
---------------------

Create consistent snapshots by ensuring services are in a stable state:

Stop service before snapshot:

.. code-block:: bash

   systemctl stop myapp
   snapm snapset create myapp-maintenance /var/lib/myapp
   systemctl start myapp

Alternatively isolate to rescue mode for system-wide consistency:

.. code-block:: bash

   systemctl isolate rescue.target
   snapm snapset create system-maintenance / /var
   systemctl isolate multi-user.target

With Backup Systems
-------------------

Integrate with existing backup workflows:

Create consistent snapshot for backup:

.. code-block:: bash

   snapm snapset create backup /home /var/lib/data

Mount individual snapshots for backup tools:

.. code-block:: bash

   mkdir -p /mnt/snapshot-backup/home
   mount /dev/fedora/home-snapset_backup_1757533502_-home /mnt/snapshot-backup/home
   ...

Run backup tools against mounted snapshot:

.. code-block:: bash

   rsync -av /mnt/snapshot-backup/home backup-server:/backups/
   ...

Clean up:

.. code-block:: bash

   umount /mnt/snapshot-backup/home
   snapm snapset delete backup

With Configuration Management
-----------------------------

Use with Ansible, Puppet, etc.:

Pre-deployment snapshot:

.. code-block:: bash

   snapm snapset create pre-deploy-$(date +%Y%m%d) /etc /var/www

Run deployment:

.. code-block:: bash

   ansible-playbook deploy.yml

Verify deployment:

.. code-block:: bash

   curl -f http://localhost/health || {
       echo "Deployment failed, reverting..."
       snapm snapset revert pre-deploy-$(date +%Y%m%d)
   }

Troubleshooting
===============

Common Issues
-------------

**Snapshot creation fails with "No space left"**

Check available space and adjust size policies:

Check current usage:

.. code-block:: bash

   vgs
   stratis pool list

Use smaller size policy:

.. code-block:: bash

   snapm snapset create backup --size-policy 50%USED /

**Boot entries not appearing**

Ensure boom is properly installed and configured:

Check boom installation:

.. code-block:: bash

   rpm -q boom-boot
   boom list

Verify boom configuration:

.. code-block:: bash

   boom profile list

**Snapshot activation fails**

Check snapshot status and storage health:

Check snapshot status:

.. code-block:: bash

   snapm snapshot list

Check LVM and Stratis status:

.. code-block:: bash

   lvs
   vgs
   stratis pool list

Check for storage errors:

.. code-block:: bash

   journalctl --priority err
   dmesg | grep -i err

**Snapshot space exhaustion**

When snapshots are close to running out of space:

Check snapshot usage:

.. code-block:: bash

   snapm snapshot list -o name,size,free

Resize snapshot if possible:

.. code-block:: bash

   snapm snapset resize my-snapset /var:500%USED

If necessary resize the LVM2 volume group, thin pool, or Stratis pool to
create more space for snapshot storage.

**Permission denied errors**

Ensure snapm is run as the root user:

.. code-block:: bash

   snapm snapset create before-upgrade / /var
   $ sudo snapm snapset create before-upgrade / /var

**LVM thin pool issues**

For thin provisioning problems:

Check thin pool status:

.. code-block:: bash

   sudo lvs -a -o +data_percent,metadata_percent

Extend thin pool if needed:

.. code-block:: bash

   sudo lvextend -L+1G /dev/vg/thin_pool

Check thin pool metadata:

.. code-block:: bash

   sudo thin_check /dev/vg/thin_pool_tmeta

**Stratis backend issues**

For Stratis-related problems:

Check Stratis daemon status:

.. code-block:: bash

   systemctl status stratisd

List Stratis pools and filesystems:

.. code-block:: bash

   stratis pool list
   stratis filesystem list

Check Stratis logs:

.. code-block:: bash

   journalctl -u stratisd

**Wrong Plugin Selected for Snapshots**

If snapshots are being created with an unexpected plugin:

1. Check current plugin priorities:

   .. code-block:: bash

      snapm plugin list

2. Verify plugin configuration files:

   .. code-block:: bash

      ls -l /etc/snapm/plugins.d/
      cat /etc/snapm/plugins.d/*.conf

3. Check which plugins can handle a given source with verbose output:

   .. code-block:: bash

      snapm -vv snapset create test /source

4. Override priorities by creating or modifying plugin configuration files in
   ``/etc/snapm/plugins.d/`` with appropriate ``PluginPriority`` values.

Remember: Higher priority values are preferred. The plugin with the highest
priority value that can handle a source will be selected.

Performance Considerations
--------------------------

**Snapshot Size Planning**

* Use ``100%SIZE`` for complete protection but higher space usage
* Use ``200%USED`` for balance between protection and space efficiency
* Use ``50%USED`` or ``25%SIZE`` for space-constrained environments

**Storage Performance**

* LVM2 thin provisioning generally offers better performance than
  copy-on-write
* Consider separate storage for snapshot backstores in high-I/O
  environments
* Monitor snapshot space usage to prevent exhaustion

Debugging
=========

Debug Mode
----------

Enable debug mode with very verbose output:

.. code-block:: bash

   snapm -d all -vv snapset create debug-test /home

Enable specific debug categories with very verbose output:

.. code-block:: bash

   snapm -vv -d command,plugin snapset list

Basic verbose output:

.. code-block:: bash

   snapm -v snapset show my-snapset

Log Analysis
------------

Check system logs for snapm timer unit activity:

Check snapm logs:

.. code-block:: bash

   journalctl --boot 0 | grep -- snapm-

Check for storage-related messages:

.. code-block:: bash

   journalctl --boot 0 | grep -i lvm
   dmesg | grep 'Buffer I\/O error'

Check for filesystem errors:

.. code-block:: bash

   journalctl --boot 0 | grep -i ext4
   journalctl --boot 0 | grep -i xfs

Storage Backend Debugging
-------------------------

**Debugging Provider/Plugin Problems**

When snapshot operations fail, check provider-specific status:

Check available plugins:

.. code-block:: bash

   snapm plugin list

For LVM2 issues, check LVM status:

.. code-block:: bash

   lvs -a
   vgs
   pvs

For Stratis issues, check daemon and pools:

.. code-block:: bash

   systemctl status stratisd
   stratis pool list
   stratis filesystem list

Configuration Validation
------------------------

Verify snapm configuration:

.. code-block:: bash

   snapm -vv --debug=all plugin list

Validate boom integration:

.. code-block:: bash

   boom list
   boom profile list

Getting Help
============

Command Line Help
-----------------

Help is available for the ``snapm`` command and each subcommand:

General help:

.. code-block:: bash

   snapm --help

Command type help:

.. code-block:: bash

   snapm snapset --help
   snapm snapshot --help
   snapm schedule --help
   snapm plugin --help

Specific command help:

.. code-block:: bash

   snapm snapset create --help
   snapm snapset list --help

Manual Pages
------------

Full command, option, argument, and configuration documentation is
available in the manual pages:

.. code-block:: bash

   man 8 snapm
   man 5 snapm.conf
   man 5 snapm-plugins.d
   man 5 snapm-schedule.d

Field Reference
---------------

Get available fields for reports:

Snapset fields:

.. code-block:: bash

   snapm snapset list -ohelp

Snapshot fields:

.. code-block:: bash

   snapm snapshot list -ohelp

Schedule fields:

.. code-block:: bash

   snapm schedule list -ohelp

Plugin fields:

.. code-block:: bash

   snapm plugin list -ohelp

Version Information
-------------------

Check snapm version and component information:

Show version:

.. code-block:: bash

   snapm --version

Show plugin versions:

.. code-block:: bash

   snapm plugin list

Online Resources
----------------

Additional help and documentation:

* **Project Homepage**: https://github.com/snapshotmanager/snapm
* **Documentation**: https://snapm.readthedocs.io/
* **Issue Tracker**: https://github.com/snapshotmanager/snapm/issues
* **Wiki**: https://github.com/snapshotmanager/snapm/wiki
* **Mailing List**: snapm-users@lists.fedoraproject.org

Community Support
-----------------

Get help from the community:

* **GitHub Issues**: https://github.com/snapshotmanager/snapm/issues

**Filing Bug Reports**

When reporting issues, include:

* Snapm version (``snapm --version``)
* Operating system and version
* Storage backend information
* Complete error messages
* Debug output (``snapm -vv --debug=all <command>``)
* Steps to reproduce the issue

**Example bug report template**:

.. code-block:: text

   **Snapm Version**: 0.4.3
   **OS**: Fedora 42 x86_64
   **Storage**: LVM2 with thin provisioning
   **Error**: Snapshot creation fails with permission denied

   **Steps to reproduce**:
   1. sudo snapm snapset create test /home
   2. Error appears immediately

   **Debug output**:
   snapm -vv -d all snapset create test /home
   [debug output here]

   **Additional context**:
   - Works with root user
   - Sudo configuration appears correct

Appendix
========

Storage Backend Details
-----------------------

**LVM2 Copy-on-Write**

* Requires fixed size allocation
* Good for infrequent snapshots
* Lower performance impact during normal operation
* Higher space requirements

**LVM2 Thin Provisioning**

* Dynamic space allocation
* Better space efficiency
* Good performance characteristics
* Requires thin pool configuration

**Stratis**

* Modern storage management
* Automatic thin provisioning
* Integrated with systemd
* Requires Stratis daemon

Exit Codes
----------

Snapm commands return standard exit codes:

* **0**: Success
* **1**: Runtime error
* **2**: Invalid arguments / parse error
