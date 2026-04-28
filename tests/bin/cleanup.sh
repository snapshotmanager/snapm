#!/bin/bash
set -euo pipefail
shopt -s nullglob

if [[ "$#" -eq 0 ]] || [[ "$1" != "--force" ]]; then
    echo -n "Clean up test suite mounts and devices? (y/n): "
    read YES
    if [[ "$YES" != "y" ]]; then
        exit 0
    fi
fi

if test -n "$(echo /var/tmp/*_snapm_mounts/*)"; then
    umount -R /var/tmp/*_snapm_mounts/* || true
fi
if test -n "$(echo /var/tmp/snapm_mnt_*/)"; then
    umount -R /var/tmp/snapm_mnt_*/ || true
fi

if [ -f /tmp/fstab ]; then
    umount /etc/fstab &> /dev/null || true
    rm -f /tmp/fstab
fi

_validate_snapm_name() {
    local name="$1"
    if [ ${#name} -lt 6 ]; then
        return 1
    fi
    local sep="${name: -5:1}"
    if [ "$sep" != "_" ]; then
        return 1
    fi
    local prefix="${name:0:${#name}-5}"
    local embedded="${name: -4}"
    local computed
    computed=$(printf '%s' "$prefix" | sha256sum | cut -c1-4)
    [ "$embedded" = "$computed" ]
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Clean up LVM VGs matching snapm_lm_*
export LVM_SYSTEM_DIR="$REPO_ROOT/tests/lvm"
for vg_dir in /dev/snapm_lm_*; do
    [ -d "$vg_dir" ] || continue
    vg_name=$(basename "$vg_dir")
    if _validate_snapm_name "$vg_name"; then
        vgremove --force --yes "$vg_name" &> /dev/null \
            || printf 'Failed to clean up %s\n' "$vg_name"
    fi
done

# Clean up Stratis pools matching snapm_st_*
for pool_dir in /dev/stratis/snapm_st_*; do
    [ -d "$pool_dir" ] || continue
    pool_name=$(basename "$pool_dir")
    if _validate_snapm_name "$pool_name"; then
        STRATIS_FILESYSTEMS=$(stratis filesystem list "$pool_name" 2>/dev/null \
            | awk -v pool="$pool_name" '$0 ~ pool {print $2}')
        for FS in $STRATIS_FILESYSTEMS; do
            if ! FAIL=$(stratis filesystem destroy "$pool_name" "$FS" 2>&1); then
                printf 'Failed to clean up %s %s: %s\n' "$pool_name" "$FS" "$FAIL"
            fi
        done
        if stratis pool list 2>/dev/null | grep -qE "^${pool_name}([[:space:]]|$)"; then
            if ! FAIL=$(stratis pool destroy "$pool_name" 2>&1); then
                printf 'Failed to clean up %s: %s\n' "$pool_name" "$FAIL"
            fi
        fi
    fi
done

LOOP_DEVICES=$(losetup --noheadings --list -Oname,back-file | awk '/_snapm_loop_back/{print $1}')
for loop in $LOOP_DEVICES; do
    losetup -d $loop || echo Failed to clean up loop device $loop
done

rm -rf /var/tmp/*_snapm_loop_back
rm -rf /var/tmp/*_snapm_mounts
rm -rf /var/tmp/*_snapm_boom_dir
