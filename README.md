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
Create a new snapset with the provided name and list of mount points.
```
# snapm snapset create [-b|--bootable] [-r|--revert] <name> mount_point...
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
default behavior on a per-mount point basis. Four policies are available:

 * FIXED - a fixed size given on the command line
 * FREE - a percentage fraction of the free space available
 * USED - a percentage fraction of the space currently used on the mount point
   (may be greater than 100%)
 * SIZE - a percentage fraction of the origin device size from 0 to 100%

The FIXED size policy accepts optional units of KiB, MiB, GiB, TiB, PiB, EiB
and ZiB. Units may be abbreviated to the first character.

Per-mount point size policies are specified by adding a ':' and the required
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

If no size policy is specified the default `200%USED` is applied. To ensure a
volume can be completely overwritten specify `100%SIZE`. This requires more
storage capacity but avoids the snapshot running out of space.

A default size policy for all mount points that do not specify an explicit
per-mount point policy can be set with the `--size-policy` argument:

```
# snapm snapset create backup --size-policy 100%SIZE / /home /var
```

On success the `snapm snapset create` command displays the newly created
snapshot set on stdout:

```
# snapm snapset create -br --size-policy 100%USED backup / /home /var
SnapsetName:      backup
MountPoints:      /, /home, /var
NrSnapshots:      3
Time:             2024-06-18 20:13:02
UUID:             9650baf8-e771-51fa-a0dd-f516447f3740
Status:           Active
Autoactivate:     yes
Bootable:         yes
BootEntries:
  SnapshotEntry:  0b98db4
  RevertEntry:    d96a135
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
SnapsetName  Time                 NrSnapshots Status  MountPoints
backup       2024-06-18 20:13:02            3 Active  /, /home, /var
upgrade      2024-06-18 20:21:29            3 Active  /, /home, /var
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
  mountpoints  - Snapshot set mount points [strlist]
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
backup       2024-06-18 20:13:02
upgrade      2024-06-18 20:21:29
```

To add fields to the default field set prefix the list of fields with the `+`
character:

```
# snapm snapset list -o+bootentry,revertentry
SnapsetName  Time                 NrSnapshots Status  MountPoints              SnapshotEntry RevertEntry
backup       2024-06-18 20:13:02            3 Active  /, /home, /var           0b98db415a504 d96a135311e15
upgrade      2024-06-18 20:21:29            3 Active  /, /home, /var           a981dad40357d d87f7dc57950a
```

The report can also be produced in JSON notation, suitable for parsing by other
tools using the `--json` argument:

```
# snapm snapset list --json
{
    "Snapsets": [
        {
            "snapset_name": "backup",
            "snapset_time": "2024-06-18 20:13:02",
            "snapset_nr_snapshots": 3,
            "snapset_status": "Active",
            "snapset_mountpoints": [
                "/",
                "/home",
                "/var"
            ]
        },
        {
            "snapset_name": "upgrade",
            "snapset_time": "2024-06-18 20:21:29",
            "snapset_nr_snapshots": 3,
            "snapset_status": "Active",
            "snapset_mountpoints": [
                "/",
                "/home",
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
MountPoints:      /, /var, /home
NrSnapshots:      3
Time:             2024-06-18 20:21:29
UUID:             bbe5a2e3-7467-5fd2-93bf-6e921b0845d7
Status:           Active
Autoactivate:     yes
Bootable:         yes
BootEntries:
  SnapshotEntry:  a981dad
  RevertEntry:    d87f7dc
```

The individual snapshots making up each set are also displayed if `--members` is
used:

```
# snapm snapset show --members
SnapsetName:      upgrade
MountPoints:      /, /var, /home
NrSnapshots:      3
Time:             2024-06-18 20:21:29
UUID:             bbe5a2e3-7467-5fd2-93bf-6e921b0845d7
Status:           Active
Autoactivate:     yes
Bootable:         yes
BootEntries:
  SnapshotEntry:  a981dad
  RevertEntry:    d87f7dc
Snapshots:
    Name:           fedora/root-snapset_upgrade_1718738489_-
    SnapsetName:    upgrade
    Origin:         /dev/fedora/root
    Time:           2024-06-18 20:21:29
    MountPoint:     /
    Provider:       lvm2-cow
    UUID:           7e6ddffe-220f-5cdf-80d7-0d7e95b88a24
    Status:         Active
    Size:           3.1GiB
    Free:           3.1GiB
    Autoactivate:   yes
    DevicePath:     /dev/fedora/root-snapset_upgrade_1718738489_-
    VolumeGroup:    fedora
    LogicalVolume:  root-snapset_upgrade_1718738489_-

    Name:           fedora/var-snapset_upgrade_1718738489_-var
    SnapsetName:    upgrade
    Origin:         /dev/fedora/var
    Time:           2024-06-18 20:21:29
    MountPoint:     /var
    Provider:       lvm2-cow
    UUID:           0a9d0467-5941-573e-99b5-041ee6693925
    Status:         Active
    Size:           1.4GiB
    Free:           1.4GiB
    Autoactivate:   yes
    DevicePath:     /dev/fedora/var-snapset_upgrade_1718738489_-var
    VolumeGroup:    fedora
    LogicalVolume:  var-snapset_upgrade_1718738489_-var

    Name:           fedora/home-snapset_upgrade_1718738489_-home
    SnapsetName:    upgrade
    Origin:         /dev/fedora/home
    Time:           2024-06-18 20:21:29
    MountPoint:     /home
    Provider:       lvm2-thin
    UUID:           81cb2222-cac5-5177-a8dc-c07701f16599
    Status:         Active
    Size:           1.0GiB
    Free:           1.9GiB
    Autoactivate:   yes
    DevicePath:     /dev/fedora/home-snapset_upgrade_1718738489_-home
    VolumeGroup:    fedora
    LogicalVolume:  home-snapset_upgrade_1718738489_-home
```

The output is also available in JSON notation using the `--json` argument:

```
# snapm snapset show upgrade --json
[
    {
        "SnapsetName": "upgrade",
        "MountPoints": [
            "/",
            "/var",
            "/home"
        ],
        "NrSnapshots": 3,
        "Timestamp": 1718738489,
        "Time": "2024-06-18 20:21:29",
        "UUID": "bbe5a2e3-7467-5fd2-93bf-6e921b0845d7",
        "Status": "Active",
        "Autoactivate": true,
        "Bootable": true,
        "BootEntries": {
            "SnapshotEntry": "a981dad",
            "RevertEntry": "d87f7dc"
        }
    }
]
```

The output is a JSON array of dictionaries describing each configured snapshot
set.

Similarly for `--members`:

```
# snapm snapset show --members --json
[
    {
        "SnapsetName": "upgrade",
        "MountPoints": [
            "/",
            "/var",
            "/home"
        ],
        "NrSnapshots": 3,
        "Timestamp": 1718738489,
        "Time": "2024-06-18 20:21:29",
        "UUID": "bbe5a2e3-7467-5fd2-93bf-6e921b0845d7",
        "Status": "Active",
        "Autoactivate": true,
        "Bootable": true,
        "BootEntries": {
            "SnapshotEntry": "a981dad",
            "RevertEntry": "d87f7dc"
        },
        "Snapshots": [
            {
                "Name": "fedora/root-snapset_upgrade_1718738489_-",
                "SnapsetName": "upgrade",
                "Origin": "/dev/fedora/root",
                "Timestamp": 1718738489,
                "Time": "2024-06-18 20:21:29",
                "MountPoint": "/",
                "Provider": "lvm2-cow",
                "UUID": "7e6ddffe-220f-5cdf-80d7-0d7e95b88a24",
                "Status": "Active",
                "Size": "3.1GiB",
                "Free": "3.1GiB",
                "SizeBytes": 3279945728,
                "FreeBytes": 3278961744,
                "Autoactivate": true,
                "DevicePath": "/dev/fedora/root-snapset_upgrade_1718738489_-"
            },
            {
                "Name": "fedora/var-snapset_upgrade_1718738489_-var",
                "SnapsetName": "upgrade",
                "Origin": "/dev/fedora/var",
                "Timestamp": 1718738489,
                "Time": "2024-06-18 20:21:29",
                "MountPoint": "/var",
                "Provider": "lvm2-cow",
                "UUID": "0a9d0467-5941-573e-99b5-041ee6693925",
                "Status": "Active",
                "Size": "1.4GiB",
                "Free": "1.4GiB",
                "SizeBytes": 1463812096,
                "FreeBytes": 1461616377,
                "Autoactivate": true,
                "DevicePath": "/dev/fedora/var-snapset_upgrade_1718738489_-var"
            },
            {
                "Name": "fedora/home-snapset_upgrade_1718738489_-home",
                "SnapsetName": "upgrade",
                "Origin": "/dev/fedora/home",
                "Timestamp": 1718738489,
                "Time": "2024-06-18 20:21:29",
                "MountPoint": "/home",
                "Provider": "lvm2-thin",
                "UUID": "81cb2222-cac5-5177-a8dc-c07701f16599",
                "Status": "Active",
                "Size": "1.0GiB",
                "Free": "1.9GiB",
                "SizeBytes": 1073741824,
                "FreeBytes": 2079837914,
                "Autoactivate": true,
                "DevicePath": "/dev/fedora/home-snapset_upgrade_1718738489_-home"
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
SnapsetName  Name                                         Origin           MountPoint       Status  Size   Free   Autoactivate Provider
upgrade      fedora/root-snapset_upgrade_1718738489_-     /dev/fedora/root /                Active  3.1GiB 3.1GiB yes          lvm2-cow
upgrade      fedora/var-snapset_upgrade_1718738489_-var   /dev/fedora/var  /var             Active  1.4GiB 1.4GiB yes          lvm2-cow
upgrade      fedora/home-snapset_upgrade_1718738489_-home /dev/fedora/home /home            Active  1.0GiB 1.9GiB yes          lvm2-thin
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
  mountpoints  - Snapshot set mount points [strlist]
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
upgrade      Active  /dev/fedora/root-snapset_upgrade_1718738489_-
upgrade      Active  /dev/fedora/var-snapset_upgrade_1718738489_-var
upgrade      Active  /dev/fedora/home-snapset_upgrade_1718738489_-home
```

To add fields to the default field set prefix the list of fields with the `+`
character:

```
# snapm snapshot list -o+devpath
SnapsetName  Name                                         Origin           MountPoint       Status  Size   Free   Autoactivate Provider  DevicePath
upgrade      fedora/root-snapset_upgrade_1718738489_-     /dev/fedora/root /                Active  3.1GiB 3.1GiB yes          lvm2-cow  /dev/fedora/root-snapset_upgrade_1718738489_-
upgrade      fedora/var-snapset_upgrade_1718738489_-var   /dev/fedora/var  /var             Active  1.4GiB 1.4GiB yes          lvm2-cow  /dev/fedora/var-snapset_upgrade_1718738489_-var
upgrade      fedora/home-snapset_upgrade_1718738489_-home /dev/fedora/home /home            Active  1.0GiB 1.9GiB yes          lvm2-thin /dev/fedora/home-snapset_upgrade_1718738489_-home
```

The report can also be produced in JSON notation, suitable for parsing by other
tools using the `--json` argument:

```
# snapm snapshot list --json
{
    "Snapshots": [
        {
            "snapset_name": "upgrade",
            "snapshot_name": "fedora/root-snapset_upgrade_1718738489_-",
            "snapshot_origin": "/dev/fedora/root",
            "snapshot_mountpoint": "/",
            "snapshot_status": "Active",
            "snapshot_size": "3.1GiB",
            "snapshot_free": "3.1GiB",
            "snapshot_autoactivate": true,
            "snapshot_provider": "lvm2-cow"
        },
        {
            "snapset_name": "upgrade",
            "snapshot_name": "fedora/var-snapset_upgrade_1718738489_-var",
            "snapshot_origin": "/dev/fedora/var",
            "snapshot_mountpoint": "/var",
            "snapshot_status": "Active",
            "snapshot_size": "1.4GiB",
            "snapshot_free": "1.4GiB",
            "snapshot_autoactivate": true,
            "snapshot_provider": "lvm2-cow"
        },
        {
            "snapset_name": "upgrade",
            "snapshot_name": "fedora/home-snapset_upgrade_1718738489_-home",
            "snapshot_origin": "/dev/fedora/home",
            "snapshot_mountpoint": "/home",
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
Name:           fedora/root-snapset_upgrade_1718738489_-
SnapsetName:    upgrade
Origin:         /dev/fedora/root
Time:           2024-06-18 20:21:29
MountPoint:     /
Provider:       lvm2-cow
UUID:           7e6ddffe-220f-5cdf-80d7-0d7e95b88a24
Status:         Active
Size:           3.1GiB
Free:           3.1GiB
Autoactivate:   yes
DevicePath:     /dev/fedora/root-snapset_upgrade_1718738489_-
VolumeGroup:    fedora
LogicalVolume:  root-snapset_upgrade_1718738489_-

Name:           fedora/var-snapset_upgrade_1718738489_-var
SnapsetName:    upgrade
Origin:         /dev/fedora/var
Time:           2024-06-18 20:21:29
MountPoint:     /var
Provider:       lvm2-cow
UUID:           0a9d0467-5941-573e-99b5-041ee6693925
Status:         Active
Size:           1.4GiB
Free:           1.4GiB
Autoactivate:   yes
DevicePath:     /dev/fedora/var-snapset_upgrade_1718738489_-var
VolumeGroup:    fedora
LogicalVolume:  var-snapset_upgrade_1718738489_-var

Name:           fedora/home-snapset_upgrade_1718738489_-home
SnapsetName:    upgrade
Origin:         /dev/fedora/home
Time:           2024-06-18 20:21:29
MountPoint:     /home
Provider:       lvm2-thin
UUID:           81cb2222-cac5-5177-a8dc-c07701f16599
Status:         Active
Size:           1.0GiB
Free:           1.9GiB
Autoactivate:   yes
DevicePath:     /dev/fedora/home-snapset_upgrade_1718738489_-home
VolumeGroup:    fedora
LogicalVolume:  home-snapset_upgrade_1718738489_-home
```

The output is also available in JSON notation using the `--json` argument:

```
# snapm snapshot show --json
[
    {
        "Name": "fedora/root-snapset_upgrade_1718738489_-",
        "SnapsetName": "upgrade",
        "Origin": "/dev/fedora/root",
        "Timestamp": 1718738489,
        "Time": "2024-06-18 20:21:29",
        "MountPoint": "/",
        "Provider": "lvm2-cow",
        "UUID": "7e6ddffe-220f-5cdf-80d7-0d7e95b88a24",
        "Status": "Active",
        "Size": "3.1GiB",
        "Free": "3.1GiB",
        "SizeBytes": 3279945728,
        "FreeBytes": 3278961744,
        "Autoactivate": true,
        "DevicePath": "/dev/fedora/root-snapset_upgrade_1718738489_-"
    },
    {
        "Name": "fedora/var-snapset_upgrade_1718738489_-var",
        "SnapsetName": "upgrade",
        "Origin": "/dev/fedora/var",
        "Timestamp": 1718738489,
        "Time": "2024-06-18 20:21:29",
        "MountPoint": "/var",
        "Provider": "lvm2-cow",
        "UUID": "0a9d0467-5941-573e-99b5-041ee6693925",
        "Status": "Active",
        "Size": "1.4GiB",
        "Free": "1.4GiB",
        "SizeBytes": 1463812096,
        "FreeBytes": 1461616377,
        "Autoactivate": true,
        "DevicePath": "/dev/fedora/var-snapset_upgrade_1718738489_-var"
    },
    {
        "Name": "fedora/home-snapset_upgrade_1718738489_-home",
        "SnapsetName": "upgrade",
        "Origin": "/dev/fedora/home",
        "Timestamp": 1718738489,
        "Time": "2024-06-18 20:21:29",
        "MountPoint": "/home",
        "Provider": "lvm2-thin",
        "UUID": "81cb2222-cac5-5177-a8dc-c07701f16599",
        "Status": "Active",
        "Size": "1.0GiB",
        "Free": "1.9GiB",
        "SizeBytes": 1073741824,
        "FreeBytes": 2079837914,
        "Autoactivate": true,
        "DevicePath": "/dev/fedora/home-snapset_upgrade_1718738489_-home"
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
```

### Reporting commands

The `snapm snapset list` and `snapm snapshot list` commands generate
a tabular report as output. To control the list of displayed fields
use the `-o/--options FIELDS` argument:

```
# snapm snapset list -oname,mountpoints
SnapsetName  MountPoints
backup       /, /home, /var
userdata     /data, /home
```

To add extra fields to the default selection, prefix the field list
with the `+` character:

```
# snapm snapset list -o+uuid
SnapsetName  Time                 NrSnapshots Status  MountPoints              SnapsetUuid
backup       2023-11-21 16:01:31            3 Active  /, /home, /var           fb76b84b-b615-5aa7-8b2c-713614794a12
userdata     2023-11-21 15:58:08            2 Active  /data, /home             6eb17db2-c95d-5fb0-8daa-5934d7c9c2d4
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
  mountpoints  - Snapshot set mount points [strlist]
  status       - Snapshot set status [str]

```

`snapshot` fields:
```
# snapm snapshot list -o help
Snapshot Fields
---------------
  snapshot_name - Snapshot name [str]
  snapshot_uuid - Snapshot UUID [uuid]
  origin        - Snapshot origin [str]
  mountpoint    - Snapshot mount point [str]
  devpath       - Snapshot device path [str]
  provider      - Snapshot provider plugin [str]
  status        - Snapshot status [str]

Snapshot set Fields
-------------------
  name          - Snapshot set name [str]
  uuid          - Snapshot set UUID [uuid]
  timestamp     - Snapshot set creation time as a UNIX epoch value [num]
  time          - Snapshot set creation time [time]
  nr_snapshots  - Number of snapshots [num]
  mountpoints   - Snapshot set mount points [strlist]
  status        - Snapshot set status [str]

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
  -d DEBUGOPTS, --debug DEBUGOPTS
                        A list of debug options to enable
  -v, --verbose         Enable verbose output
  -V, --version         Report the version number of snapm
```

Subcommand help:

```
# snapm snapset --help
usage: snapm snapset [-h] {create,delete,rename,activate,deactivate,list,show} ...

positional arguments:
  {create,delete,rename,activate,deactivate,list,show}
    create              Create snapshot sets
    delete              Delete snapshot sets
    rename              Rename a snapshot set
    activate            Activate snapshot sets
    deactivate          Deactivate snapshot sets
    list                List snapshot sets
    show                Display snapshot sets

options:
  -h, --help            show this help message and exit

# snapm snapset create --help
usage: snapm snapset create [-h] [-b] [-r] SNAPSET_NAME MOUNT_POINT [MOUNT_POINT ...]

positional arguments:
  SNAPSET_NAME    The name of the snapshot set to create
  MOUNT_POINT     A list of mount points to include in this snapshot set

options:
  -h, --help      show this help message and exit
  -b, --bootable  Create a boot entry for this snapshot set
  -r, --revert  Create a revert boot entry for this snapshot set
```

## Documentation

API [documentation][2] is automatically generated using [Sphinx][3]
and [Read the Docs][4].

Installation and user documentation will be added in a future update.

## License

The snapm project is licensed under the GNU General Public License version 2.

The stratislib package (used by the snapm Stratis plugin) is based on code
from the [stratis-cli][6] project and is licensed under the Apache-2.0 license.

 [1]: https://github.com/snapshotmanager/snapm/issues
 [2]: https://snapm.readthedocs.io/en/latest/
 [3]: http://sphinx-doc.org/
 [4]: https://www.readthedocs.org/
 [5]: https://github.com/snapshotmanager/boom
 [6]: https://github.com/stratis-storage/stratis-cli

