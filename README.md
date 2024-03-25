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
                * [rollback](#rollback)
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
         * [Reporting commands](#reporting-commands)
         * [Getting help](#getting-help)
      * [Patches and pull requests](#patches-and-pull-requests)
      * [Documentation](#documentation)

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

### Building an RPM package
A spec file is included in the repository that can be used to build RPM packages
of snapm. The packit service is also enabled for new pull requests and will
automatically build packages for fedora-stable and fedora-development.

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
# snapm snapset create [-b|--bootable] [-r|--rollback] <name> mount_point...
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

##### rollback
Roll back an existing snapset, re-setting the content of the origin volumes
to the state they were in at the time the snapset was created. The snapset
to be rolled back may be specified either by its name or uuid.

Rolling back a snapshot set with mounted and in-use origin volumes will
schedule the roll back to take place the next time that the volumes are
activated, for example by booting into a configured rollback boot entry for
the snapshot set.
```
# snapm snapset rollback <name|uuid>
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

##### show
Display available snapsets matching selection criteria.
```
# snapm snapset show [<name|uuid>]
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

##### snapshot show
Display available snapshots matching selection criteria.
```
# snapm snapshot show [<name|uuid>]
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

### Getting help
Help is available for the `snapm` command and each subcommand.

Run the command with `--help` to display the full usage message:

```
# snapm --help
usage: snapm [-h] [-d DEBUGOPTS] [-v] [-V] {snapset,snapshot} ...

Snapshot Manager

positional arguments:
  {snapset,snapshot}    Command type
    snapset             Snapshot set commands
    snapshot            Snapshot commands

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
  -r, --rollback  Create a rollback boot entry for this snapshot set
```

## Documentation

API [documentation][2] is automatically generated using [Sphinx][3]
and [Read the Docs][4].

Installation and user documentation will be added in a future update.

 [1]: https://github.com/snapshotmanager/snapm/issues
 [2]: https://snapm.readthedocs.io/en/latest/
 [3]: http://sphinx-doc.org/
 [4]: https://www.readthedocs.org/
