![Snapm CI](https://github.com/snapshotmanager/snapm/actions/workflows/snapm.yml/badge.svg)
# Snapm

Snapshot manager (snapm) is a tool for managing sets of snapshots on Linux
systems.  The snapm tool allows snapshots of multiple volumes to be captured at
the same time, representing the system state at the time the set was created.

The tool has a modular plugin architecture allowing different snapshot backends
to be used together. Currently snapshots using LVM2 copy-on-write and thinly
provisioned snapshots are supported.

   * [Snapm](#snapm)
      * [Reporting bugs](#reporting-bugs)
      * [Building and installing Snapm](#building-and-installing-snapm)
         * [Building an RPM package](#building-an-rpm-package)
      * [The snapm command](#the-snapm-command)
         * [Snapshot sets](#snapshot-sets)
         * [Snapm subcommands](#snapm-subcommands)
            * [snapset](#snapset)
                * [create](#create)
                * [delete](#delete)
                * [rename](#rename)
                * [revert](#revert)
                * [resize](#resize)
                * [split](#split)
                * [activate](#activate)
                * [deactivate](#deactivate)
                * [autoactivate](#autoactivate)
                * [list](#list)
                * [show](#show)
            * [snapshot](#snapshot)
                * [activate](#snapshot-activate)
                * [deactivate](#snapshot-deactivate)
                * [autoactivate](#snapshot-autoactivate)
                * [list](#snapshot-list)
                * [show](#snapshot-show)
            * [plugin](#plugin)
                * [list](#plugin-list)
         * [Reporting commands](#reporting-commands)
         * [Getting help](#getting-help)
      * [Patches and pull requests](#patches-and-pull-requests)
      * [Documentation](#documentation)
      * [License](#license)

Snapm aims to be a simple and extensible, and to be able to create snapshots for
a wide range of Linux system configurations.

This project is hosted at:

  * http://github.com/snapshotmanager/snapm

For the latest version, to contribute, and for more information, please visit
the project pages.

To clone the current main (development) branch run:

```
git clone git://github.com/snapshotmanager/snapm.git
```
## Reporting bugs

Please report bugs by opening an issue in the [GitHub Issue Tracker][1]

## Building and installing Snapm

A `setuptools` based build script is provided: local installations and
package builds can be performed by running `python setup.py` and a
setup command. See `python setup.py --help` for detailed information on
the available options and commands.

The `snapm` command uses the [boom boot manager][5] to manage boot entries for
the snapshots that it creates. If installing `snapm` manually, Install `boom`
first: either from your distribution repositories or the upstream Git repo.
Snapshot manager is tested with boom-1.6.4 and later.

### Building an RPM package
A spec file is included in the repository that can be used to build RPM packages
of snapm. The packit service is also enabled for new pull requests and will
automatically build packages for fedora-stable, fedora-development, epel-9,
centos-stream-9, and centos-stream-10.

## The snapm command

The `snapm` command is the main interface to the snapshot manager.  It is able
to create, delete, and display snapshots and snapshot sets and provides reports
listing the snapshots and snapshot sets available on the system.

Snapm commands normally operate on a particular object type: a snapshot set or an
individual snapshot.

```
# snapm snapset <command> <options> # `snapset` command
```

```
# snapm snapshot <command> <options> # `snapshot` command
```

### Snapshot sets

Snapshot manager groups snapshots taken at the same moment in time into a named
"snapshot set", or snapset. This allows the snapshots to be managed as a single
unit and simplifies the management of snapshots of multiple volumes. Each
snapshot set must contain at least one snapshot.

### Snapm subcommands
#### snapset
The `snapset` subcommand is used to create, delete, rename, activate,
deactivate, list and display snapsets.

##### create
Create a new snapset with the provided name and list of sources (mount point
or block device paths):
```
# snapm snapset create [-b|--bootable] [-r|--revert] <name> source...
```
###### Size Policies
Size policies can be used to control the size of fixed-sized snapshots and to
check for available space when creating a snapshot set.

Some snapshot implementations (Lvm2CoW) require a fixed size to be specified
for the snapshot backstore when the snapshot is created. The default size
allocated by snapshot manager is 2x the current file system usage, allowing the
existing content of the volume to be overwritten twice before exhausting the
snapshot space.

Plugins for snapshot providers that do not require a fixed snapshot size will
check that space is available for the requested size policy at snapshot set
creation time.

Size policy hints can be manually given on the command line to override the
default behavior on a per-source path basis. Four policies are available:

 * FIXED - a fixed size given on the command line
 * FREE - a percentage fraction of the free space available
 * USED - a percentage fraction of the space currently used on the mount point
   (may be greater than 100%)
 * SIZE - a percentage fraction of the origin device size from 0 to 100%

The FIXED size policy accepts optional units of KiB, MiB, GiB, TiB, PiB, EiB
and ZiB. Units may be abbreviated to the first character.

The USED size policy can only be applied to mounted file systems.

Per-source path size policies are specified by adding a ':' and the required
policy to the corresponding mount point path, for example:


```
# snapm snapset create backup /:2G /var:1G /home
```

```
# snapm snapset create backup /:25%FREE /var:25%FREE /home
```

```
# snapm snapset create backup /:100%USED /var:100%USED /home
```

```
# snapm snapset create backup /:100%SIZE /var:100%SIZE /home
```

If no size policy is specified the default is `200%USED` for mounted file
systems and 25%SIZE for unmounted block devices. To ensure a volume can be
completely overwritten specify `100%SIZE`. This requires more storage capacity
but avoids the possibility of the snapshot running out of space.

A default size policy for all source paths that do not specify an explicit
per-path policy can be set with the `--size-policy` argument:

```
# snapm snapset create backup --size-policy 100%SIZE / /home /var
```

On success the `snapm snapset create` command displays the newly created
snapshot set on stdout:

```
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
```

##### delete
Delete an existing snapset by name or uuid.
```
# snapm snapset delete <name|uuid>
```

##### rename
Rename an existing snapset.
```
# snapm snapset rename <old_name> <new_name>
```

##### revert
Revert an existing snapset, re-setting the content of the origin volumes
to the state they were in at the time the snapset was created. The snapset
to be reverted may be specified either by its name or uuid.

```
# snapm snapset revert <name|uuid>
```

If the origins of the snapshot set are in use at the time of the revert the
operation is deferred until the next time the snapshot set is activated (for
example during a reboot). If a revert boot entry was created for the snapshot
set the `revert` command will suggest booting into it to continue:

```
# snapm snapset revert upgrade
WARNING - Snaphot set upgrade origin is in use: reboot required to complete revert
Boot into 'Revert upgrade 2024-06-10 15:25:15 (6.8.9-300.fc40.x86_64)' to continue
```

##### resize
Resize the members on an existing snapset, applying a new size policy to
specified sources or applying a new default size policy to all snapshots within
a snapset.

Resize the `/var` member of the snapshot set named upgrade to 100%SIZE:

```
# snapm snapset resize upgrade /var:100%SIZE
```

Resize each member of the snapshot set named backup to the 200%USED size
policy:

```
# snapm snapset resize backup --size-policy 200%USED
```

##### split
Split snapshots from an existing snapshot set into a new snapshot set.

Split the snapshot set named 'name' into a new snapshot set named
'new\_name'. Each listed source from 'name' is split into the new
snapshot set. Sources that are not listed on the command line remain part of
the original snapshot set. It is an error to split all sources from a
snapshot set: in this case use 'snapm snapset rename' instead.

To split the source "/home" from the existing snapshot set "upgrade" into a
new snapshot set named "noupgrade":

```
# snapm snapset split upgrade noupgrade /home
SnapsetName:      noupgrade
Sources:          /home
NrSnapshots:      1
Time:             2025-03-31 20:21:29
UUID:             30e69b86-5c48-5e5d-be1a-bf3d63aef8f7
Status:           Inactive
Autoactivate:     no
Bootable:         no
```

##### activate
Activate the members of an existing snapset, or all snapsets if no name or
uuid argument is given.
```
# snapm snapset activate [<name|uuid>]
```

##### deactivate
Deactivate the members of an existing snapset, or all snapsets if no name or
uuid argument is given.
```
# snapm snapset deactivate [<name|uuid>]
```

##### autoactivate
Enable or disable autoactivation for the snapshots in a snapshot set.
```
# snapm snapset autoactivate [--yes|--no] [<name|uuid>]
```

##### list
List available snapsets matching selection criteria.
```
# snapm snapset list [<name|uuid>]
```

By default the information is presented as a tabular report with column headings
indicating the meaning of each value. The default column selection includes the
SnapsetName, Time, NrSnapshots, Status, and MountPoints fields:

```
# snapm snapset list
SnapsetName  Time                 NrSnapshots Status  Sources
backup       2024-12-05 19:14:03            3 Active  /, /home, /var
upgrade      2024-12-05 19:14:09            2 Active  /, /var
```

Custom field specifications may be given with the `-o`/`--options` argument. To
obtain a list of available fields run `snapm snapset list -o+help`:

```
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

```

To specify custom fields pass a comma separated list to `-o`:

```
# snapm snapset list -oname,time
SnapsetName  Time
backup       2024-12-05 19:14:03
upgrade      2024-12-05 19:14:09
```

To add fields to the default field set prefix the list of fields with the `+`
character:

```
# snapm snapset list -o+bootentry,revertentry
SnapsetName  Time                 NrSnapshots Status  Sources        SnapshotEntry RevertEntry
backup       2024-12-05 19:14:03            3 Active  /, /home, /var 41573a414f9d5 1cc5bc59c9b90
upgrade      2024-12-05 19:14:09            2 Active  /, /var        a60dab4d3fb36 4ce6b27f16f30
```

The report can also be produced in JSON notation, suitable for parsing by other
tools using the `--json` argument:

```
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
```

For further report formatting options refer to the `snapm(8)` manual page.

##### show
Display available snapsets matching selection criteria.
```
# snapm snapset show [<name|uuid>]
```

By default the output is formatted in the same way as the output of the `snapm
snapset create` command:

```
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
```


The individual snapshots making up each set are also displayed if `--members` is
used:

```
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
```

The output is also available in JSON notation using the `--json` argument:

```
# snapm snapset show upgrade --json
[
    {
        "SnapsetName": "upgrade",
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
        "Timestamp": 1733426370,
        "Time": "2024-12-05 19:19:30",
        "UUID": "f0a46cde-9eed-5335-b239-66ed53e5b503",
        "Status": "Active",
        "Autoactivate": true,
        "Bootable": true,
        "BootEntries": {
            "SnapshotEntry": "dfce8d8",
            "RevertEntry": "fc414b0"
        }
    }
]
```

The output is a JSON array of dictionaries describing each configured snapshot
set.

Similarly for `--members`:

```
# snapm snapset show upgrade --members --json
[
    {
        "SnapsetName": "upgrade",
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
        "Timestamp": 1733426370,
        "Time": "2024-12-05 19:19:30",
        "UUID": "f0a46cde-9eed-5335-b239-66ed53e5b503",
        "Status": "Active",
        "Autoactivate": true,
        "Bootable": true,
        "BootEntries": {
            "SnapshotEntry": "dfce8d8",
            "RevertEntry": "fc414b0"
        },
        "Snapshots": [
            {
                "Name": "fedora/root-snapset_upgrade_1733426370_-",
                "SnapsetName": "upgrade",
                "Origin": "/dev/fedora/root",
                "Timestamp": 1733426370,
                "Time": "2024-12-05 19:19:30",
                "Source": "/",
                "MountPoint": "/",
                "Provider": "lvm2-cow",
                "UUID": "7566dde3-96f4-5288-8b15-18be7c520327",
                "Status": "Active",
                "Size": "8.8GiB",
                "Free": "8.8GiB",
                "SizeBytes": 9474932736,
                "FreeBytes": 9473985242,
                "Autoactivate": true,
                "DevicePath": "/dev/fedora/root-snapset_upgrade_1733426370_-"
            },
            {
                "Name": "fedora/var-snapset_upgrade_1733426370_-var",
                "SnapsetName": "upgrade",
                "Origin": "/dev/fedora/var",
                "Timestamp": 1733426370,
                "Time": "2024-12-05 19:19:30",
                "Source": "/var",
                "MountPoint": "/var",
                "Provider": "lvm2-cow",
                "UUID": "22674f3e-f5c4-5632-9add-2df51985679e",
                "Status": "Active",
                "Size": "6.4GiB",
                "Free": "6.4GiB",
                "SizeBytes": 6891241472,
                "FreeBytes": 6890552347,
                "Autoactivate": true,
                "DevicePath": "/dev/fedora/var-snapset_upgrade_1733426370_-var"
            }
        ]
    }
]
```

#### snapshot
The `snapshot` command is used to manipulate, list, and display snapshots.

##### snapshot activate
Activate individual snapshots matching selection criteria.
```
# snapm snapshot activate [-N name] [-U uuid] [<name|uuid>]
```

##### snapshot deactivate
Deactivate individual snapshots matching selection criteria.
```
# snapm snapshot deactivate [-N name] [-U uuid] [<name|uuid>]
```

##### snapshot autoactivate
Enable or disable autoactivation for individual snapshots matching selection
criteria.
```
# snapm snapshot autoactivate [--yes|--no] [-N name] [-U uuid] [<name|uuid>]
```

##### snapshot list
List available snapshots matching selection criteria.
```
# snapm snapshot list [<name|uuid>]
```

By default the information is presented as a tabular report with column headings
indicating the meaning of each value. The default column selection includes the
SnapsetName, Name, Origin, MountPoint, Status, Size, Free, Autoactivate, and
Provider fields:

```
# snapm snapshot list
SnapsetName  Name                                         Origin           Source  Status  Size   Free   Autoactivate Provider
upgrade      fedora/root-snapset_upgrade_1733426499_-     /dev/fedora/root /       Active  8.8GiB 8.8GiB yes          lvm2-cow
upgrade      fedora/var-snapset_upgrade_1733426499_-var   /dev/fedora/var  /var    Active  6.4GiB 6.4GiB yes          lvm2-cow
upgrade      fedora/home-snapset_upgrade_1733426499_-home /dev/fedora/home /home   Active  1.0GiB 1.9GiB yes          lvm2-thin
```

Custom field specifications may be given with the `-o`/`--options` argument. To
obtain a list of available fields run `snapm snapset list -o+help`:

```
# snapm snapshot list -o+help
Snapshot Fields
---------------
  name         - Snapshot name [str]
  uuid         - Snapshot UUID [uuid]
  origin       - Origin [str]
  source       - Snapshot source [str]
  mountpoint   - Snapshot mount point [str]
  devpath      - Snapshot device path [str]
  provider     - Snapshot provider plugin [str]
  status       - Snapshot status [str]
  size         - Snapshot size [size]
  free         - Free space available [size]
  size_bytes   - Snapshot size in bytes [num]
  free_bytes   - Free space available in bytes [num]
  autoactivate - Autoactivation status [str]

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

```

To specify custom fields pass a comma separated list to `-o`:

```
# snapm snapshot list -osnapset_name,status,devpath
SnapsetName  Status  DevicePath
upgrade      Active  /dev/fedora/root-snapset_upgrade_1733426499_-
upgrade      Active  /dev/fedora/var-snapset_upgrade_1733426499_-var
upgrade      Active  /dev/fedora/home-snapset_upgrade_1733426499_-home
```

To add fields to the default field set prefix the list of fields with the `+`
character:

```
# snapm snapshot list -o+devpath
SnapsetName  Name                                         Origin           Source  Status  Size   Free   Autoactivate Provider  DevicePath
upgrade      fedora/root-snapset_upgrade_1733426499_-     /dev/fedora/root /       Active  8.8GiB 8.8GiB yes          lvm2-cow  /dev/fedora/root-snapset_upgrade_1733426499_-
upgrade      fedora/var-snapset_upgrade_1733426499_-var   /dev/fedora/var  /var    Active  6.4GiB 6.4GiB yes          lvm2-cow  /dev/fedora/var-snapset_upgrade_1733426499_-var
upgrade      fedora/home-snapset_upgrade_1733426499_-home /dev/fedora/home /home   Active  1.0GiB 1.9GiB yes          lvm2-thin /dev/fedora/home-snapset_upgrade_1733426499_-home
```

The report can also be produced in JSON notation, suitable for parsing by other
tools using the `--json` argument:

```
# snapm snapshot list --json
{
    "Snapshots": [
        {
            "snapset_name": "upgrade",
            "snapshot_name": "fedora/root-snapset_upgrade_1733426499_-",
            "snapshot_origin": "/dev/fedora/root",
            "snapshot_source": "/",
            "snapshot_status": "Active",
            "snapshot_size": "8.8GiB",
            "snapshot_free": "8.8GiB",
            "snapshot_autoactivate": true,
            "snapshot_provider": "lvm2-cow"
        },
        {
            "snapset_name": "upgrade",
            "snapshot_name": "fedora/var-snapset_upgrade_1733426499_-var",
            "snapshot_origin": "/dev/fedora/var",
            "snapshot_source": "/var",
            "snapshot_status": "Active",
            "snapshot_size": "6.4GiB",
            "snapshot_free": "6.4GiB",
            "snapshot_autoactivate": true,
            "snapshot_provider": "lvm2-cow"
        },
        {
            "snapset_name": "upgrade",
            "snapshot_name": "fedora/home-snapset_upgrade_1733426499_-home",
            "snapshot_origin": "/dev/fedora/home",
            "snapshot_source": "/home",
            "snapshot_status": "Active",
            "snapshot_size": "1.0GiB",
            "snapshot_free": "1.9GiB",
            "snapshot_autoactivate": true,
            "snapshot_provider": "lvm2-thin"
        }
    ]
}
```

For further report formatting options refer to the `snapm(8)` manual page.

##### snapshot show
Display available snapshots matching selection criteria.
```
# snapm snapshot show [<name|uuid>]
```

By default the output is formatted in the same way as the output of the `snapm
snapset show --members` command:

```
# snapm snapshot show
Name:           fedora/root-snapset_upgrade_1733426499_-
SnapsetName:    upgrade
Origin:         /dev/fedora/root
Time:           2024-12-05 19:21:39
Source:         /
MountPoint:     /
Provider:       lvm2-cow
UUID:           6883146c-38e8-50a7-ac37-c2e9e809bb10
Status:         Active
Size:           8.8GiB
Free:           8.8GiB
Autoactivate:   yes
DevicePath:     /dev/fedora/root-snapset_upgrade_1733426499_-
VolumeGroup:    fedora
LogicalVolume:  root-snapset_upgrade_1733426499_-

Name:           fedora/var-snapset_upgrade_1733426499_-var
SnapsetName:    upgrade
Origin:         /dev/fedora/var
Time:           2024-12-05 19:21:39
Source:         /var
MountPoint:     /var
Provider:       lvm2-cow
UUID:           1a0ab3f1-f995-5c7b-846b-78189cf62e06
Status:         Active
Size:           6.4GiB
Free:           6.4GiB
Autoactivate:   yes
DevicePath:     /dev/fedora/var-snapset_upgrade_1733426499_-var
VolumeGroup:    fedora
LogicalVolume:  var-snapset_upgrade_1733426499_-var

Name:           fedora/home-snapset_upgrade_1733426499_-home
SnapsetName:    upgrade
Origin:         /dev/fedora/home
Time:           2024-12-05 19:21:39
Source:         /home
MountPoint:     /home
Provider:       lvm2-thin
UUID:           e8163739-b700-5ef8-974a-7da53eeaff04
Status:         Active
Size:           1.0GiB
Free:           1.9GiB
Autoactivate:   yes
DevicePath:     /dev/fedora/home-snapset_upgrade_1733426499_-home
VolumeGroup:    fedora
LogicalVolume:  home-snapset_upgrade_1733426499_-home
```

The output is also available in JSON notation using the `--json` argument:

```
# snapm snapshot show --json
[
    {
        "Name": "fedora/root-snapset_upgrade_1733426499_-",
        "SnapsetName": "upgrade",
        "Origin": "/dev/fedora/root",
        "Timestamp": 1733426499,
        "Time": "2024-12-05 19:21:39",
        "Source": "/",
        "MountPoint": "/",
        "Provider": "lvm2-cow",
        "UUID": "6883146c-38e8-50a7-ac37-c2e9e809bb10",
        "Status": "Active",
        "Size": "8.8GiB",
        "Free": "8.8GiB",
        "SizeBytes": 9474932736,
        "FreeBytes": 9473985242,
        "Autoactivate": true,
        "DevicePath": "/dev/fedora/root-snapset_upgrade_1733426499_-"
    },
    {
        "Name": "fedora/var-snapset_upgrade_1733426499_-var",
        "SnapsetName": "upgrade",
        "Origin": "/dev/fedora/var",
        "Timestamp": 1733426499,
        "Time": "2024-12-05 19:21:39",
        "Source": "/var",
        "MountPoint": "/var",
        "Provider": "lvm2-cow",
        "UUID": "1a0ab3f1-f995-5c7b-846b-78189cf62e06",
        "Status": "Active",
        "Size": "6.4GiB",
        "Free": "6.4GiB",
        "SizeBytes": 6891241472,
        "FreeBytes": 6890552347,
        "Autoactivate": true,
        "DevicePath": "/dev/fedora/var-snapset_upgrade_1733426499_-var"
    },
    {
        "Name": "fedora/home-snapset_upgrade_1733426499_-home",
        "SnapsetName": "upgrade",
        "Origin": "/dev/fedora/home",
        "Timestamp": 1733426499,
        "Time": "2024-12-05 19:21:39",
        "Source": "/home",
        "MountPoint": "/home",
        "Provider": "lvm2-thin",
        "UUID": "e8163739-b700-5ef8-974a-7da53eeaff04",
        "Status": "Active",
        "Size": "1.0GiB",
        "Free": "1.9GiB",
        "SizeBytes": 1073741824,
        "FreeBytes": 2079837914,
        "Autoactivate": true,
        "DevicePath": "/dev/fedora/home-snapset_upgrade_1733426499_-home"
    }
]
```

The output is a JSON array of dictionaries describing each configured snapshot.

#### plugin
The `plugin` command is used to display information on the available snapshot
provider plugins.

##### plugin list

The `plugin list` command lists the available plugins:

```
# snapm plugin list
PluginName PluginVersion PluginType
lvm2-cow   0.1.0         Lvm2CowSnapshot
lvm2-thin  0.1.0         Lvm2ThinSnapshot
stratis    0.1.0         StratisSnapshot
```

### Reporting commands

The `snapm snapset list` and `snapm snapshot list` commands generate
a tabular report as output. To control the list of displayed fields
use the `-o/--options FIELDS` argument:

```
# snapm snapset list -oname,sources
SnapsetName  Sources
backup       /, /home, /var
userdata     /data, /home
```

To add extra fields to the default selection, prefix the field list
with the `+` character:

```
# snapm snapset list -o+uuid
SnapsetName  Time                 NrSnapshots Status   Sources        UUID
backup       2024-12-05 19:26:28            3 Active   /, /home, /var 53514020-e88d-5f53-bf09-42c6ab6e325d
userdata     2024-12-05 19:26:45            2 Inactive /data, /home   e8d58051-7a94-5802-8328-54661ab1a70f
```

To display the available fields for either report use the field
name `help`.

`snapset` fields:

```
# snapm snapset list -o help
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

```

`snapshot` fields:

```
# snapm snapshot list -o help
Snapshot Fields
---------------
  name         - Snapshot name [str]
  uuid         - Snapshot UUID [uuid]
  origin       - Origin [str]
  source       - Snapshot source [str]
  mountpoint   - Snapshot mount point [str]
  devpath      - Snapshot device path [str]
  provider     - Snapshot provider plugin [str]
  status       - Snapshot status [str]
  size         - Snapshot size [size]
  free         - Free space available [size]
  size_bytes   - Snapshot size in bytes [num]
  free_bytes   - Free space available in bytes [num]
  autoactivate - Autoactivation status [str]

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

```

#### JSON reports

All reports can optionally be formatted as JSON for parsing by other tools using
the `--json` argument:

```
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
```

### Getting help
Help is available for the `snapm` command and each subcommand.

Run the command with `--help` to display the full usage message:

```
# snapm --help
usage: snapm [-h] [-d DEBUGOPTS] [-v] [-V] {snapset,snapshot,plugin} ...

Snapshot Manager

positional arguments:
  {snapset,snapshot,plugin}
                        Command type
    snapset             Snapshot set commands
    snapshot            Snapshot commands
    plugin              Plugin commands

options:
  -h, --help            show this help message and exit
  -d, --debug DEBUGOPTS
                        A list of debug options to enable
  -v, --verbose         Enable verbose output
  -V, --version         Report the version number of snapm
```

Subcommand help:

```
# snapm snapset --help
usage: snapm snapset [-h]
                     {create,delete,rename,resize,revert,activate,deactivate,autoactivate,list,show} ...

positional arguments:
  {create,delete,rename,resize,revert,activate,deactivate,autoactivate,list,show}
    create              Create snapshot sets
    delete              Delete snapshot sets
    rename              Rename a snapshot set
    resize              Resize snapshot sets
    revert              Revert snapshot sets
    activate            Activate snapshot sets
    deactivate          Deactivate snapshot sets
    autoactivate        Set autoactivation status for snapshot sets
    list                List snapshot sets
    show                Display snapshot sets

options:
  -h, --help            show this help message and exit
```

```
# snapm snapset create --help
usage: snapm snapset create [-h] [-s SIZE_POLICY] [-b] [-r]
                            SNAPSET_NAME SOURCE [SOURCE ...]

positional arguments:
  SNAPSET_NAME          The name of the snapshot set to create
  SOURCE                A device or mount point path to include in this
                        snapshot set

options:
  -h, --help            show this help message and exit
  -s, --size-policy SIZE_POLICY
                        A default size policy for fixed size snapshots
  -b, --bootable        Create a boot entry for this snapshot set
  -r, --revert          Create a revert boot entry for this snapshot set
```

## Documentation

API [documentation][2] is automatically generated using [Sphinx][3]
and [Read the Docs][4].

Installation and user documentation will be added in a future update.

## License

The snapm project is licensed under the the Apache license, version 2.0
(Apache-2.0):

  https://www.apache.org/licenses/LICENSE-2.0


 [1]: https://github.com/snapshotmanager/snapm/issues
 [2]: https://snapm.readthedocs.io/en/latest/
 [3]: http://sphinx-doc.org/
 [4]: https://www.readthedocs.org/
 [5]: https://github.com/snapshotmanager/boom
 [6]: https://github.com/stratis-storage/stratis-cli

