![Snapm CI](https://github.com/snapshotmanager/snapm/actions/workflows/snapm.yml/badge.svg) [![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0) [![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/snapshotmanager/snapm)

# Snapm

Snapshot manager (snapm) manages coordinated sets of snapshots on Linux
systems. It orchestrates snapshots across multiple volumes to capture
a consistent point‑in‑time system state.

Supported backends: LVM2 (copy-on‑write and thin-provisioned) and Stratis.

## Features

- **Coordinated snapshot sets**: Create coordinated snapshots across
  multiple volumes
- **Bootable snapshots**: Generate boot entries for snapshot boot and
  failsafe revert and rollback
- **Multiple backends**: Support for LVM2 (copy-on-write and thin) and
  Stratis
- **Flexible scheduling**: Automated snapshot creation with retention
  policies
- **Size policies**: Flexible snapshot sizing with multiple strategies
- **Plugin architecture**: Extensible design for additional storage
  backends

## Quick Start

### Installation

Note: Commands show a root prompt (`#`). When copying, omit the leading
`#`.

#### From RPM (Fedora/CentOS/RHEL)

Install from distribution repositories (when available):

```bash
# dnf install snapm boom-boot
```

#### Manual Installation

Clone ``boom-boot`` and ``snapm`` repositories and install using pip:

```bash
# git clone https://github.com/snapshotmanager/boom-boot.git
# cd boom-boot
# python3 -m pip install .
```

```bash
# git clone https://github.com/snapshotmanager/snapm.git
# cd snapm
# python3 -m pip install .
```

### Basic Usage

#### Create a bootable snapshot set

Create a snapshot set named "before-upgrade" of root and home,
with snapshot boot and revert boot entries:

```bash
# snapm snapset create --bootable --revert before-upgrade / /home
```

#### List snapshot sets

```bash
# snapm snapset list
```

Show detailed information:

```bash
# snapm snapset show before-upgrade
```

#### Boot into a snapshot

- Reboot and select the snapshot boot entry from the GRUB menu
- Entry will be named: "Snapshot before-upgrade YYYY-MM-DD HH:MM:SS
(version)"
- Optionally run ``grub2-reboot <title>`` to pre-select snapshot boot
  entry, e.g.:
  ``grub2-reboot "Snapshot before-upgrade YYYY-MM-DD HH:MM:SS (version)"``

#### Revert to snapshot state

```bash
# snapm snapset revert before-upgrade
```

Then reboot into the Revert boot entry.

#### Clean up

Delete snapshot set when no longer needed:

```bash
# snapm snapset delete before-upgrade
```

### Common Workflows

#### System Updates

Before major system updates:

```bash
# snapm snapset create --bootable --revert pre-update / /var
```

If update goes wrong, revert:

```bash
# snapm snapset revert pre-update
```

When satisfied with update, clean up:

```bash
# snapm snapset delete pre-update
```

#### Development Snapshots

Quick development checkpoint:

```bash
# snapm snapset create dev-checkpoint /home /var
```

Continue working...

If needed, revert specific volumes:

```bash
# snapm snapset split dev-checkpoint dev-checkpoint-home /home
```

Example output:

```text
SnapsetName:      dev-checkpoint-home
Sources:          /home
NrSnapshots:      1
Time:             2025-08-30 12:44:00
UUID:             ee82269f-8c78-5814-b47a-b9be31bcebb5
Status:           Inactive
Autoactivate:     no
Bootable:         no
```

Then revert the split-off snapshot set:

```bash
# snapm snapset revert dev-checkpoint-home
```

Alternatively revert the entire snapshot set:

```bash
# snapm snapset revert dev-checkpoint
```

## Documentation

- **[User Guide](doc/user_guide.rst)** - Comprehensive usage
  documentation
- **[API Documentation](https://snapm.readthedocs.io/)** -
  Auto-generated API docs
- **[Manual Pages](man/)** - System manual pages

## Requirements

- Linux system with LVM2 or Stratis volumes
- [boom-boot](https://github.com/snapshotmanager/boom-boot)
- Root privileges for performing storage operations
- Python 3.9+

## Supported Storage Backends

| Backend | Type | Snapshots | Thin Provisioning | Status |
|---------|------|-----------|-------------------|--------|
| LVM2 | Copy-on-Write | ✓ | ✗ | Stable |
| LVM2 | Thin Pools | ✓ | ✓ | Stable |
| Stratis | Thin Pools | ✓ | ✓ | Stable |

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md)
for guidelines.

### Development Setup
```bash
# git clone https://github.com/snapshotmanager/snapm.git
# cd snapm
# python3 -m pip install -e .
```

### Testing

Run tests:

```bash
# pytest -v --log-level=debug tests/
```

Run with coverage:

```bash
# coverage run /usr/bin/pytest -v --log-level=debug tests/
```

## Support

- **Issues**: [GitHub Issue Tracker](https://github.com/snapshotmanager/snapm/issues)
- **Discussions**: [GitHub Discussions](https://github.com/snapshotmanager/snapm/discussions)
- **Documentation**: [Read the Docs](https://snapm.readthedocs.io/)

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.

## Related Projects

- [boom](https://github.com/snapshotmanager/boom) - Boot manager for
  Linux snapshot boot
- [stratis-cli](https://github.com/stratis-storage/stratis-cli) -
  Command-line tool for Stratis storage
- [lvm2](https://gitlab.com/lvmteam/lvm2) - LVM2 logical volume manager
