============
User's Guide
============

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

Command Reference
=================

The ``snapm`` Command
---------------------

The ``snapm`` command provides the CLI interface to the snapshot
manager. It is able to create, delete, and display snapshots and
snapshot sets and provides reports listing the snapshots, snapshot
sets, and schedules available on the system.

Each snapm commands operate on a particular object type: a snapshot set,
an individual snapshot, or a schedule.

.. code-block:: bash

   # snapm snapset <command> <options>  # snapset command

.. code-block:: bash

   # snapm snapshot <command> <options> # snapshot command

Snapset Commands
================

The ``snapset`` subcommand is used to create, delete, rename, activate,
deactivate, list and display snapshot sets.

snapset create
--------------

Create a new snapshot set with the provided name and list of sources
(mount point or block device paths):

.. code-block:: bash

   # snapm snapset create [-b|--bootable] [-r|--revert] <name> source...

Per-source path size policies are specified by adding a ':' and the
required policy to the corresponding mount point path, for example:

.. code-block:: bash

   # snapm snapset create backup /:2G /var:1G /home

.. code-block:: bash

   # snapm snapset create backup /:25%FREE /var:25%FREE /home

.. code-block:: bash

   # snapm snapset create backup /:100%USED /var:100%USED /home

.. code-block:: bash

   # snapm snapset create backup /:100%SIZE /var:100%SIZE /home

If no size policy is specified the default is ``200%USED`` for mounted
file systems and 25%SIZE for unmounted block devices. To ensure a volume
can be completely overwritten specify ``100%SIZE``. This requires more
storage capacity but avoids the possibility of the snapshot running out
of space.

A default size policy for all source paths that do not specify an
explicit per-path policy can be set with the ``--size-policy`` argument:

.. code-block:: bash

   # snapm snapset create backup --size-policy 100%SIZE / /home /var

On success the ``snapm snapset create`` command displays the newly
created snapshot set on stdout:

.. code-block:: bash

   # snapm snapset create -br --size-policy 100%USED backup / /home /var
   SnapsetName:      backup
   Sources:          /, /home, /var
   NrSnapshots:      3
   Time:             2024-12-05 19:10:44
   UUID:             d3f5e3cd-a383-5dba-b597-9134a2c426e9
   Status:           Active
   Autoactivate:     yes
   Bootable:         yes
   BootEntries:
     SnapshotEntry:  f574a20
     RevertEntry:    f428f9f

Autoindex for recurring snapshot sets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``--autoindex`` argument allows creating a recurring snapshot set
with a common basename and unique index (a non-negative integer). This
can be used to take regular snapshots with a common name:

.. code-block:: bash

   # snapm snapset create hourly --autoindex /:25%SIZE /var:25%SIZE
   SnapsetName:      hourly.3
   Sources:          /, /var
   NrSnapshots:      2
   Time:             2025-03-26 14:17:18
   UUID:             ae082452-7995-5316-ac65-388eadd9879c
   Status:           Active
   Autoactivate:     yes
   Bootable:         no

The basename and index values are available via the ``snapset list``
report:

.. code-block:: bash

   # snapm snapset list -o+basename,index
   SnapsetName  Time                 NrSnapshots Status  Sources  Basename     Index
   backup       2025-03-25 18:12:54            2 Invalid /, /var  backup           -
   hourly.0     2025-03-25 19:40:39            2 Invalid /, /var  hourly           0
   hourly.1     2025-03-26 14:17:11            2 Active  /, /var  hourly           1
   hourly.2     2025-03-26 14:17:15            2 Active  /, /var  hourly           2
   hourly.3     2025-03-26 14:17:18            2 Active  /, /var  hourly           3

snapset delete
--------------

Delete an existing snapset by name or uuid.

.. code-block:: bash

   # snapm snapset delete <name|uuid>

snapset rename
--------------

Rename an existing snapset.

.. code-block:: bash

   # snapm snapset rename <old_name> <new_name>

snapset revert
--------------

Revert an existing snapset, re-setting the content of the origin volumes
to the state they were in at the time the snapset was created. The
snapset to be reverted may be specified either by its name or uuid.

.. code-block:: bash

   # snapm snapset revert <name|uuid>

If the origins of the snapshot set are in use at the time of the revert
the operation is deferred until the next time the snapshot set is
activated (for example during a reboot). If a revert boot entry was
created for the snapshot set the ``revert`` command will suggest booting
into it to continue:

.. code-block:: bash

   # snapm snapset revert upgrade
   WARNING - Snapshot set upgrade origin is in use: reboot required to complete revert
   Boot into 'Revert upgrade 2024-06-10 15:25:15 (6.8.9-300.fc40.x86_64)' to continue

snapset resize
--------------

Resize the members on an existing snapset, applying a new size policy to
specified sources or applying a new default size policy to all snapshots
within a snapset.

Resize the ``/var`` member of the snapshot set named upgrade to
100%SIZE:

.. code-block:: bash

   # snapm snapset resize upgrade /var:100%SIZE

Resize each member of the snapshot set named backup to the 200%USED size
policy:

.. code-block:: bash

   # snapm snapset resize backup --size-policy 200%USED

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

   # snapm snapset split upgrade noupgrade /home
   SnapsetName:      noupgrade
   Sources:          /home
   NrSnapshots:      1
   Time:             2025-03-31 20:21:29
   UUID:             30e69b86-5c48-5e5d-be1a-bf3d63aef8f7
   Status:           Inactive
   Autoactivate:     no
   Bootable:         no

snapset activate
----------------

Activate the members of an existing snapset, or all snapsets if no name
or uuid argument is given.

.. code-block:: bash

   # snapm snapset activate [<name|uuid>]

snapset deactivate
------------------

Deactivate the members of an existing snapset, or all snapsets if no
name or uuid argument is given.

.. code-block:: bash

   # snapm snapset deactivate [<name|uuid>]

snapset autoactivate
--------------------

Enable or disable autoactivation for the snapshots in a snapshot set.

.. code-block:: bash

   # snapm snapset autoactivate [--yes|--no] [<name|uuid>]

snapset list
------------

List available snapsets matching selection criteria.

.. code-block:: bash

   # snapm snapset list [<name|uuid>]

By default the information is presented as a tabular report with column
headings indicating the meaning of each value. The default column
selection includes the SnapsetName, Time, NrSnapshots, Status, and
Sources fields:

.. code-block:: bash

   # snapm snapset list
   SnapsetName  Time                 NrSnapshots Status  Sources
   backup       2024-12-05 19:14:03            3 Active  /, /home, /var
   upgrade      2024-12-05 19:14:09            2 Active  /, /var

Custom field specifications may be given with the ``-o``/``--options``
argument. To obtain a list of available fields run ``snapm snapset list
-o+help``:

.. code-block:: bash

   # snapm snapset list -o+help
   Snapshot set Fields
   -------------------
     name         - Snapshot set name [str]
     uuid         - Snapshot set UUID [uuid]
     timestamp    - Snapshot set creation time as a UNIX epoch value [num]
     time         - Snapshot set creation time [time]
     nr_snapshots - Number of snapshots [num]
     sources      - Snapshot set sources [strlist]
     mountpoints  - Snapshot set mount points [strlist]
     devices      - Snapshot set devices [strlist]
     status       - Snapshot set status [str]
     autoactivate - Autoactivation status [str]
     bootable     - Configured for snapshot boot [str]
     bootentry    - Snapshot set boot entry [sha]
     revertentry  - Snapshot set revert boot entry [sha]

To specify custom fields pass a comma separated list to ``-o``:

.. code-block:: bash

   # snapm snapset list -oname,time
   SnapsetName  Time
   backup       2024-12-05 19:14:03
   upgrade      2024-12-05 19:14:09

To add fields to the default field set prefix the list of fields with
the ``+`` character:

.. code-block:: bash

   # snapm snapset list -o+bootentry,revertentry
   SnapsetName  Time                 NrSnapshots Status  Sources        SnapshotEntry RevertEntry
   backup       2024-12-05 19:14:03            3 Active  /, /home, /var 41573a414f9d5 1cc5bc59c9b90
   upgrade      2024-12-05 19:14:09            2 Active  /, /var        a60dab4d3fb36 4ce6b27f16f30

The report can also be produced in JSON notation, suitable for parsing
by other tools using the ``--json`` argument:

.. code-block:: bash

   # snapm snapset list --json
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
               ]
           },
           {
               "snapset_name": "upgrade",
               "snapset_time": "2024-12-05 19:14:09",
               "snapset_nr_snapshots": 2,
               "snapset_status": "Active",
               "snapset_sources": [
                   "/",
                   "/var"
               ]
           }
       ]
   }

For further report formatting options refer to the ``snapm(8)`` manual
page.

snapset show
------------

Display available snapsets matching selection criteria.

.. code-block:: bash

   # snapm snapset show [<name|uuid>]

By default the output is formatted in the same way as the output of the
``snapm snapset create`` command:

.. code-block:: bash

   # snapm snapset show upgrade
   SnapsetName:      upgrade
   Sources:          /, /var
   NrSnapshots:      2
   Time:             2024-12-05 19:14:09
   UUID:             87c6df8f-bd8c-5c9d-b081-4f6d6068cc07
   Status:           Active
   Autoactivate:     yes
   Bootable:         yes
   BootEntries:
     SnapshotEntry:  a60dab4
     RevertEntry:    4ce6b27

The individual snapshots making up each set are also displayed if
``--members`` is used:

.. code-block:: bash

   # snapm snapset show --members
   SnapsetName:      upgrade
   Sources:          /, /var
   NrSnapshots:      2
   Time:             2024-12-05 19:19:30
   UUID:             f0a46cde-9eed-5335-b239-66ed53e5b503
   Status:           Active
   Autoactivate:     yes
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

  # snapm snapset show --json before-upgrade
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
          "Autoactivate": true,
          "Bootable": true,
          "BootEntries": {
              "SnapshotEntry": "7c56dc0",
              "RevertEntry": "457f733"
          }
      }
  ]

Snapshot Commands
=================

The ``snapshot`` command is used to manipulate, list, and display
snapshots.

snapshot activate
-----------------

Activate individual snapshots matching selection criteria.

.. code-block:: bash

   # snapm snapshot activate [-N name] [-U uuid] [<name|uuid>]

snapshot deactivate
-------------------

Deactivate individual snapshots matching selection criteria.

.. code-block:: bash

   # snapm snapshot deactivate [-N name] [-U uuid] [<name|uuid>]

snapshot autoactivate
---------------------

Enable or disable autoactivation for individual snapshots matching
selection criteria.

.. code-block:: bash

   # snapm snapshot autoactivate [--yes|--no] [-N name] [-U uuid] [<name|uuid>]

snapshot list
-------------

List available snapshots matching selection criteria.

.. code-block:: bash

   # snapm snapshot list [<name|uuid>]

By default the information is presented as a tabular report with column
headings indicating the meaning of each value. The default column
selection includes the SnapsetName, Name, Origin, Source, Status,
Size, Free, Autoactivate, and Provider fields:

.. code-block:: bash

   # snapm snapshot list
   SnapsetName  Name                                         Origin           Source  Status  Size   Free   Autoactivate Provider
   upgrade      fedora/root-snapset_upgrade_1733426499_-     /dev/fedora/root /       Active  8.8GiB 8.8GiB yes          lvm2-cow
   upgrade      fedora/var-snapset_upgrade_1733426499_-var   /dev/fedora/var  /var    Active  6.4GiB 6.4GiB yes          lvm2-cow
   upgrade      fedora/home-snapset_upgrade_1733426499_-home /dev/fedora/home /home   Active  1.0GiB 1.9GiB yes          lvm2-thin

snapshot show
-------------

Display available snapshots matching selection criteria.

.. code-block:: bash

   # snapm snapshot show [<name|uuid>]

Plugin Commands
===============

The ``plugin`` command is used to display information on the available
snapshot provider plugins.

plugin list
-----------

The ``plugin list`` command lists the available plugins:

.. code-block:: bash

   # snapm plugin list
   PluginName PluginVersion PluginType
   lvm2-cow   0.1.0         Lvm2CowSnapshot
   lvm2-thin  0.1.0         Lvm2ThinSnapshot
   stratis    0.1.0         StratisSnapshot

Schedule Commands
=================

The ``schedule`` command is used to create, display and manage schedules
for automatic snapshot set creation.

schedule create
---------------

Create a new schedule with the provided name and list of sources (mount
point or block device paths):

.. code-block:: bash

   # snapm schedule create [-a|--autoindex] [-b|--bootable] [-r|--revert] [--size-policy policy] [-p|--policy-type policy_type] [--keep-count count] [--keep-years years] [--keep-months months] [--keep-weeks weeks] [--keep-days days] [--keep-yearly yearly] [--keep-quarterly quarterly] [--keep-monthly monthly] [--keep-weekly weekly] [--keep-daily daily] [--keep-hourly hourly] --calendarspec calendarspec <name> source...

.. code-block:: bash

   # snapm schedule create --policy-type count --keep-count 2 --bootable --revert --size-policy 25%SIZE --calendarspec hourly hourly / /var
   Name: hourly
   SourceSpecs: /, /var
   DefaultSizePolicy: 25%SIZE
   Autoindex: False
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

   # snapm schedule delete <name>

schedule enable
---------------

Enable an existing schedule by name:

.. code-block:: bash

   # snapm schedule enable <name>

schedule disable
----------------

Disable an existing schedule by name:

.. code-block:: bash

   # snapm schedule disable <name>

schedule list
-------------

List configured schedules:

.. code-block:: bash

   # snapm schedule list [--nameprefixes] [--noheadings] [--options fields] [--sort fields] [--rows|--json] [--separator separator]

.. code-block:: bash

   # snapm schedule list
   ScheduleName ScheduleSources         SizePolicy Autoindex OnCalendar     Enabled
   custom       /, /home:100%SIZE, /var 50%SIZE    yes       *-*-1 01:00:00 yes
   monthly      /:25%SIZE, /var:25%SIZE            yes       monthly        no
   hourly       /, /var                 25%SIZE    no        hourly         yes

schedule show
-------------

Display configured schedule:

.. code-block:: bash

   # snapm schedule show <name>

schedule gc
-----------

Run configured garbage collection policy for schedule:

.. code-block:: bash

   # snapm schedule gc <name>

Reporting Commands
==================

The ``snapm snapset list``, ``snapm snapshot list`` ``snapm plugin
list``, and ``snapm schedule list`` commands generate a tabular report
as output. To control the list of displayed fields use the
``-o``/``--options FIELDS`` argument:

.. code-block:: bash

   # snapm snapset list -oname,sources
   SnapsetName  Sources
   backup       /, /home, /var
   userdata     /data, /home

To add extra fields to the default selection, prefix the field list with
the ``+`` character:

.. code-block:: bash

   # snapm snapset list -o+uuid
   SnapsetName  Time                 NrSnapshots Status   Sources        UUID
   backup       2024-12-05 19:26:28            3 Active   /, /home, /var 53514020-e88d-5f53-bf09-42c6ab6e325d
   userdata     2024-12-05 19:26:45            2 Inactive /data, /home   e8d58051-7a94-5802-8328-54661ab1a70f

To display the available fields for either report use the field name
``help``:

.. code-block:: bash

  # snapm snapset list -ohelp
  Snapshot set Fields
  -------------------
    name         - Snapshot set name [str]
    uuid         - Snapshot set UUID [uuid]
    basename     - Snapshot set basename [str]
    index        - Snapshot set index [idx]
    timestamp    - Snapshot set creation time as a UNIX epoch value [num]
    time         - Snapshot set creation time [time]
    nr_snapshots - Number of snapshots [num]
    sources      - Snapshot set sources [strlist]
    mountpoints  - Snapshot set mount points [strlist]
    devices      - Snapshot set devices [strlist]
    status       - Snapshot set status [str]
    autoactivate - Autoactivation status [str]
    bootable     - Configured for snapshot boot [str]
    bootentry    - Snapshot set boot entry [sha]
    revertentry  - Snapshot set revert boot entry [sha]

JSON Reports
------------

All reports can optionally be formatted as JSON for parsing by other
tools using the ``--json`` argument:

.. code-block:: bash

   # snapm plugin list --json
   {
       "Plugins": [
           {
               "plugin_name": "lvm2-cow",
               "plugin_version": "0.1.0",
               "plugin_type": "Lvm2CowSnapshot"
           },
           {
               "plugin_name": "lvm2-thin",
               "plugin_version": "0.1.0",
               "plugin_type": "Lvm2ThinSnapshot"
           },
           {
               "plugin_name": "stratis",
               "plugin_version": "0.1.0",
               "plugin_type": "StratisSnapshot"
           }
       ]
   }

Common Use Cases
================

System Updates and Rollback
---------------------------

Before performing system updates, create a bootable snapshot:

Create snapshot set before update:

.. code-block:: bash

   # snapm snapset create --bootable --revert pre-update / /var

Perform system update:

.. code-block:: bash

   # dnf update

If update causes issues, revert:

.. code-block:: bash

   # snapm snapset revert pre-update

Or boot into snapshot from boot menu (select: "Snapshot pre-update
YYYY-MM-DD HH:MM:SS (version)"):

.. code-block:: bash

   # reboot

Clean up when satisfied with update:

.. code-block:: bash

   # snapm snapset delete pre-update

Development and Testing
-----------------------

Create development checkpoints for experimental work:

* Create checkpoint before major changes:

.. code-block:: bash

   # snapm snapset create dev-checkpoint /home /var/lib/myapp

* Make experimental changes

* If changes don't work out, revert:

.. code-block:: bash

   # snapm snapset revert dev-checkpoint

Data Protection
---------------

Regular data snapshots for important directories allow backups to be
captured from snapshots, lowering production impact by reducing backup
downtime.

Daily backup of user data:

.. code-block:: bash

   # snapm snapset create daily-backup /home /var/lib/database

Weekly system snapshots:

.. code-block:: bash

   # snapm snapset create weekly-system / /var --size-policy 50%SIZE

Automated Scheduling
--------------------

Set up automated snapshot schedules:

Hourly snapshots, keep 24:

.. code-block:: bash

   # snapm schedule create --autoindex \
       --policy-type count \
       --keep-count 24 \
       --calendarspec hourly \
       hourly / /home

Daily snapshots, keep 7 days:

.. code-block:: bash

   # snapm schedule create --autoindex \
       --policy-type age \
       --keep-days 7 \
       --calendarspec daily \
       daily / /home /var

Monthly bootable snapshots, keep 12:

.. code-block:: bash

   # snapm schedule create --autoindex \
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

   # snapm snapset create config-backup /etc /var/lib/config

Make configuration changes:

.. code-block:: bash

   # vim /etc/myapp/config.conf

Test changes...

If configuration breaks system, revert:

.. code-block:: bash

   # snapm snapset revert config-backup

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

   # snapm snapshot list -o name,size,free

Clean up old snapshots:

.. code-block:: bash

   # snapm snapset delete old-snapshot-name

Review scheduled snapshots:

.. code-block:: bash

   # snapm schedule list

Security Considerations
=======================

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
  * Consider password protecting GRUB and physical access controls.

Integration Examples
====================

With Systemd Services
---------------------

Create consistent snapshots by ensuring services are in a stable state:

Stop service before snapshot:

.. code-block:: bash

   # systemctl stop myapp
   # snapm snapset create myapp-maintenance /var/lib/myapp
   # systemctl start myapp

Alternatively isolate to rescue mode for system-wide consistency:

.. code-block:: bash

   # systemctl isolate rescue.target
   # snapm snapset create system-maintenance / /var
   # systemctl isolate multi-user.target

With Backup Systems
-------------------

Integrate with existing backup workflows:

Create consistent snapshot for backup:

.. code-block:: bash

   # snapm snapset create backup-source /home /var/lib/data

Mount individual snapshots for backup tools:

.. code-block:: bash

   # mkdir /mnt/snapshot-backup
   # mount /dev/fedora/home-snapset_backup-source_* /mnt/snapshot-backup

Run backup tools against mounted snapshot:

.. code-block:: bash

   # rsync -av /mnt/snapshot-backup/ backup-server:/backups/

Clean up:

.. code-block:: bash

   # umount /mnt/snapshot-backup
   # snapm snapset delete backup-source

With Configuration Management
-----------------------------

Use with Ansible, Puppet, etc.:

Pre-deployment snapshot:

.. code-block:: bash

   # snapm snapset create pre-deploy-$(date +%Y%m%d) /etc /var/www

Run deployment:

.. code-block:: bash

   # ansible-playbook deploy.yml

Verify deployment:

.. code-block:: bash

   # curl -f http://localhost/health || {
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

   # vgs
   # stratis pool

Use smaller size policy:

.. code-block:: bash

   # snapm snapset create backup --size-policy 50%USED /

**Boot entries not appearing**

Ensure boom is properly installed and configured:

Check boom installation:

.. code-block:: bash

   # rpm -q boom-boot
   # boom list

Verify boom configuration:

.. code-block:: bash

   # boom profile list

**Snapshot activation fails**

Check snapshot status and storage health:

Check snapshot status:

.. code-block:: bash

   # snapm snapshot list

Check LVM and Stratis status:

.. code-block:: bash

   # lvs
   # vgs
   # stratis pool

Check for storage errors:

.. code-block:: bash

   # journalctl --priority err
   # dmesg | grep -i err

**Snapshot space exhaustion**

When snapshots are close to running out of space:

Check snapshot usage:

.. code-block:: bash

   # snapm snapshot list -o name,size,free

Resize snapshot if possible:

.. code-block:: bash

   # snapm snapset resize my-snapset /var:500%USED

If necessary resize the LVM2 volume group, thin pool, or Stratis pool to
create more space for snapshot storage.

**Permission denied errors**

Ensure snapm is run as the root user:

.. code-block:: bash

   # snapm snapset create before-upgrade / /var
   $ sudo snapm snapset create before-upgrade / /var

**LVM thin pool issues**

For thin provisioning problems:

Check thin pool status:

.. code-block:: bash

   # sudo lvs -a -o +data_percent,metadata_percent

Extend thin pool if needed:

.. code-block:: bash

   # sudo lvextend -L+1G /dev/vg/thin_pool

Check thin pool metadata:

.. code-block:: bash

   # sudo thin_check /dev/vg/thin_pool_tmeta

**Stratis backend issues**

For Stratis-related problems:

Check Stratis daemon status:

.. code-block:: bash

   # systemctl status stratisd

List Stratis pools and filesystems:

.. code-block:: bash

   # stratis pool list
   # stratis filesystem list

Check Stratis logs:

.. code-block:: bash

   # journalctl -u stratisd

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

Enable debug output for troubleshooting:

Enable debug mode with verbose output:

.. code-block:: bash

   # snapm -d all -vv snapset create debug-test /home

Enable specific debug categories with verbose output:

.. code-block:: bash

   # snapm -vv -d command,plugin -v snapset list

Basic verbose output:

.. code-block:: bash

   # snapm -vv snapset show my-snapset

Log Analysis
------------

Check system logs for snapm activity:

Check snapm logs:

.. code-block:: bash

   # journalctl --boot 0 | grep -- snapm-

Check for storage-related messages:

.. code-block:: bash

   # journalctl --boot 0 | grep -i lvm
   # dmesg | grep 'Buffer I\/O error'

Check for filesystem errors:

.. code-block:: bash

   # journalctl --boot 0 | grep -i ext4
   # journalctl --boot 0 | grep -i xfs

Storage Backend Debugging
-------------------------

**Debugging Provider/Plugin Problems**

When snapshot operations fail, check provider-specific status:

Check available plugins:

.. code-block:: bash

   # snapm plugin list

For LVM2 issues, check LVM status:

.. code-block:: bash

   # lvs -a
   # vgs
   # pvs

For Stratis issues, check daemon and pools:

.. code-block:: bash

   # systemctl status stratisd
   # stratis pool list
   # stratis filesystem list

Configuration Validation
------------------------

Verify snapm configuration:

.. code-block:: bash

   # snapm -vv --debug=all plugin list

Validate boom integration:

.. code-block:: bash

   # boom list
   # boom profile list

Getting Help
============

Command Line Help
-----------------

Help is available for the ``snapm`` command and each subcommand:

General help:

.. code-block:: bash

   # snapm --help

Command type help:

.. code-block:: bash

   # snapm snapset --help
   # snapm snapshot --help
   # snapm schedule --help
   # snapm plugin --help

Specific command help:

.. code-block:: bash

   # snapm snapset create --help
   # snapm snapset list --help

Manual Pages
------------

Full command, option, argument, and configuration documentation is
available in the manual pages:

.. code-block:: bash

   # man 8 snapm
   # man 5 snapm.conf

Field Reference
---------------

Get available fields for reports:

Snapset fields:

.. code-block:: bash

   # snapm snapset list -o help

Snapshot fields:

.. code-block:: bash

   # snapm snapshot list -o help

Schedule fields:

.. code-block:: bash

   # snapm schedule list -o help

Plugin fields:

.. code-block:: bash

   # snapm plugin list -o help

Version Information
-------------------

Check snapm version and component information:

Show version:

.. code-block:: bash

   # snapm --version

Show plugin versions:

.. code-block:: bash

   # snapm plugin list

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
* Debug output (``snapm -vv --debug all <command>``)
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
   # snapm -d all snapset create test /home
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
