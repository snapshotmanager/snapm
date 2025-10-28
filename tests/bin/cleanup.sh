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

if [ -d /dev/test_vg0 ]; then
	export LVM_SYSTEM_DIR="$PWD/tests/lvm"
	vgremove --force --yes test_vg0 &> /dev/null || echo Failed to clean up test_vg0 || true
fi

if [ -d /dev/stratis/pool1 ]; then
	STRATIS_FILESYSTEMS=$(stratis filesystem list pool1 | awk '/pool1/{print $2}')
	for FS in $STRATIS_FILESYSTEMS; do
	    if ! FAIL=$(stratis filesystem destroy pool1 "$FS" 2>&1); then
	        printf 'Failed to clean up pool1 %s: %s\n' "$FS" "$FAIL"
	    fi
	done
fi

if stratis pool list | grep -qE '^pool1([[:space:]]|$)'; then
	if ! FAIL=$(stratis pool destroy pool1 2>&1); then
		printf 'Failed to clean up pool1: %s\n' "$FAIL"
	fi
fi

LOOP_DEVICES=$(losetup --noheadings --list -Oname,back-file | awk '/_snapm_loop_back/{print $1}')
for loop in $LOOP_DEVICES; do
    losetup -d $loop || echo Failed to clean up loop device $loop
done

rm -rf /var/tmp/*_snapm_loop_back
rm -rf /var/tmp/*_snapm_mounts
rm -rf /var/tmp/*_snapm_boom_dir
