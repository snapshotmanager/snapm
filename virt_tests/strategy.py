# Copyright Red Hat
#
# virt_tests/strategy.py - Snapshot Manager end-to-end testing strategy.
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Test Strategy Framework for snapm End-to-End Testing
Provides OS-specific test strategies with ordered test execution
"""
import inspect
import json
import shlex
import traceback
import time
from abc import ABC, abstractmethod
from typing import Dict, List

from .testvm import SnapmTestVM, setup_test_vm
from .util import log_print, err_print


class TestStrategy(ABC):
    """Base class for OS-specific test strategies"""

    name = None

    def __init__(self, vm: SnapmTestVM, vg: str):
        self.vm = vm
        self.vg = vg
        self.test_results: Dict[str, bool] = {}
        self.snapset_name = "before-upgrade"

    def execute_tests(self) -> bool:
        """Execute all test_* methods in lexicographic order"""

        # Get all test methods and sort by name
        test_methods = []
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if name.startswith("test_") and callable(method):
                test_methods.append((name, method))

        test_methods.sort(key=lambda x: x[0])  # Sort by method name

        log_print(
            f"Executing {len(test_methods)} test steps for {self.__class__.__name__}"
        )

        # Execute tests in order
        for test_name, test_method in test_methods:
            log_print(f"⚙️ Running {test_name}...")

            try:
                success = test_method()
                self.test_results[test_name] = success

                if success:
                    log_print(f"✅ {test_name} passed")
                else:
                    err_print(f"❌ {test_name} failed")
                    return False

            except Exception as e:
                err_print(f"❌ {test_name} error: {e}")
                err_print(traceback.format_exc())
                self.test_results[test_name] = False
                return False

        log_print("✅ All tests passed")
        return True

    @abstractmethod
    def get_package_manager(self) -> str:
        """Return the package manager command for this OS"""
        pass

    def get_test_packages(self) -> List[str]:
        """Return list of packages to install for testing updates"""
        return ["kernel", "glibc"]

    def get_test_results(self) -> Dict[str, bool]:
        """Return test execution results"""
        return self.test_results

    def validate_entry_title(self, title: str) -> bool:
        """
        Validate boot entry title for safe shell usage.

        Args:
            title: The boot entry title to validate

        Returns:
            bool: True if title is safe to use in shell commands, False otherwise
        """
        if not title:
            return False

        title = title.strip()
        if not title:
            return False

        # Avoid shell expansion and injection: disallow quotes and command-substitution chars
        unsafe_chars = ("'", '"', "$", "`", "\\")
        if any(ch in title for ch in unsafe_chars):
            err_print(f"Malformed boot title (contains unsafe chars): {title}")
            return False

        return True

    def reboot_to_boot_entry(self, entry_type: str, entry_description: str) -> bool:
        """Helper method to reboot to a specific boot entry from the current snapset"""

        # Get snapset info to find the specified entry
        snapset_info = self.vm.get_command_output(
            f"snapm snapset show {self.snapset_name} --json"
        )
        if not snapset_info:
            return False

        try:
            data = json.loads(snapset_info)
            if not data or not isinstance(data, list) or len(data) == 0:
                return False

            snapset = data[0]
            boot_entry = snapset.get("BootEntries", {}).get(entry_type)

            if not boot_entry:
                err_print(f"No {entry_description} entry found")
                return False

            boot_id_arg = shlex.quote(boot_entry)
            # Get boot title from boom
            boot_title = self.vm.get_command_output(
                f"boom list --noheadings -otitle --boot-id {boot_id_arg}"
            )
            if boot_title:
                boot_title = boot_title.strip()
            if not boot_title:
                err_print(
                    f"Could not get boot title for {entry_description} entry {boot_entry}"
                )
                return False

            if not self.validate_entry_title(boot_title):
                return False

            log_print(f"⏩ Rebooting to {entry_description} entry: {boot_title}")

            # Set next boot to the entry
            if not self.vm.run_command(f'grub2-reboot "{boot_title}"'):
                return False

            # Reboot to the entry
            if not self.vm.run_command("reboot", check=False):
                return False

            # Wait for system to come back up
            time.sleep(30)  # Give time for reboot
            return self.vm.wait_for_ssh(timeout=300)

        except json.JSONDecodeError:
            return False

    def test_10_verify_system_ready(self) -> bool:
        """Verify system is ready for testing"""
        # Check snapm is installed
        version_output = self.vm.get_command_output("snapm --version")
        if not version_output:
            err_print("snapm is not installed or not functioning")
            return False

        log_print(f"Found snapm version: {version_output.strip()}")

        # Verify LVM setup
        vgs_output = self.vm.get_command_output("vgs --noheadings")
        if not vgs_output or self.vg not in vgs_output:
            err_print(f"Volume group '{self.vg}' not found in system")
            return False

        return True

    def test_20_create_baseline(self) -> bool:
        """Create baseline system state before testing"""
        commands = [
            # Create test data directory
            "mkdir -p /opt/test-baseline",
            # Record installed packages
            f"{self.get_package_manager()} list --installed > /opt/test-baseline/packages-before.txt",
            # Create test files with known content
            "echo 'original-content' > /opt/test-baseline/test-file.txt",
            "echo 'config-before-update' > /etc/test-config.conf",
        ]

        return all(self.vm.run_command(cmd) for cmd in commands)

    def test_30_create_snapset(self) -> bool:
        """Create bootable snapset before applying changes"""
        # Create snapset with revert capability
        result = self.vm.get_command_output(
            f"snapm snapset create -br {self.snapset_name} / /var"
        )
        if not result:
            return False

        log_print(f"Created snapset: {self.snapset_name}")
        log_print(result)

        # Verify snapset was created
        snapsets = self.vm.get_command_output("snapm snapset list --json")
        if not snapsets:
            return False

        try:
            data = json.loads(snapsets)
            snapset_exists = any(
                s["snapset_name"] == self.snapset_name for s in data.get("Snapsets", [])
            )
            return snapset_exists
        except json.JSONDecodeError:
            return False

    def test_40_apply_system_changes(self) -> bool:
        """Apply system changes that we want to test rollback from"""

        # Install test packages
        test_packages = " ".join(self.get_test_packages())
        if not self.vm.run_command(
            f"{self.get_package_manager()} update -y {test_packages}",
            timeout=1200,
        ):
            return False

        # Modify test files
        commands = [
            "echo 'modified-content' > /opt/test-baseline/test-file.txt",
            "echo 'config-after-update' > /etc/test-config.conf",
            "echo 'new-file-after-update' > /opt/test-baseline/new-file.txt",
        ]

        return all(self.vm.run_command(cmd) for cmd in commands)

    def test_50_verify_changes_applied(self) -> bool:
        """Verify that the system changes were actually applied"""

        # Check file modifications
        content = self.vm.get_command_output("cat /opt/test-baseline/test-file.txt")
        if not content or content.strip() != "modified-content":
            return False

        config = self.vm.get_command_output("cat /etc/test-config.conf")
        if not config or config.strip() != "config-after-update":
            return False

        # Check new file exists
        return bool(self.vm.get_command_output("ls /opt/test-baseline/new-file.txt"))

    def test_60_reboot_to_snapshot(self) -> bool:
        """Reboot into snapshot entry to verify snapshot state"""
        return self.reboot_to_boot_entry("SnapshotEntry", "snapshot")

    def test_70_verify_snapshot_state(self) -> bool:
        """Verify system is in snapshot state (original baseline)"""

        # Check file content is original
        content = self.vm.get_command_output("cat /opt/test-baseline/test-file.txt")
        if not content or content.strip() != "original-content":
            err_print(
                f"Snapshot file content wrong: got '{content}', expected 'original-content'"
            )
            return False

        config = self.vm.get_command_output("cat /etc/test-config.conf")
        if not config or config.strip() != "config-before-update":
            err_print(
                f"Snapshot config wrong: got '{config}', expected 'config-before-update'"
            )
            return False

        # Check that new file doesn't exist in snapshot
        if self.vm.get_command_output("ls /opt/test-baseline/new-file.txt 2>/dev/null"):
            err_print("New file exists in snapshot (should not)")
            return False

        return True

    def test_80_reboot_to_updated_system(self) -> bool:
        """Reboot back to the updated system"""

        # Reboot to default (updated) system
        if not self.vm.run_command("reboot", check=False):
            return False

        # Wait for system to come back up
        time.sleep(30)
        return self.vm.wait_for_ssh(timeout=300)

    def test_90_verify_back_in_updated_system(self) -> bool:
        """Verify we're back in the updated system state"""

        # Check we're in updated state
        content = self.vm.get_command_output("cat /opt/test-baseline/test-file.txt")
        if not content or content != "modified-content":
            err_print(
                f"Not in updated state: got '{content}', expected 'modified-content'"
            )
            return False

        config = self.vm.get_command_output("cat /etc/test-config.conf")
        if not config or config.strip() != "config-after-update":
            return False

        # Check new file exists
        if not self.vm.get_command_output("ls /opt/test-baseline/new-file.txt"):
            err_print("New file missing from updated system")
            return False

        return True

    def test_95_initiate_revert(self) -> bool:
        """Initiate revert to snapshot state"""

        # Start the revert process first
        if not self.vm.run_command(f"snapm snapset revert {self.snapset_name}"):
            return False

        # Then reboot to the revert entry
        return self.reboot_to_boot_entry("RevertEntry", "revert")

    def test_99_verify_final_rollback_success(self) -> bool:
        """Verify system was successfully rolled back to snapshot state"""

        # Check file content was reverted
        content = self.vm.get_command_output("cat /opt/test-baseline/test-file.txt")
        if not content or content.strip() != "original-content":
            err_print(
                f"File content not reverted: got '{content}', expected 'original-content'"
            )
            return False

        config = self.vm.get_command_output("cat /etc/test-config.conf")
        if not config or config.strip() != "config-before-update":
            err_print(
                f"Config not reverted: got '{config}', expected 'config-before-update'"
            )
            return False

        # Check that new file is gone
        if self.vm.get_command_output("ls /opt/test-baseline/new-file.txt 2>/dev/null"):
            err_print("New file still exists after rollback")
            return False

        # Verify snapset status
        snapsets = self.vm.get_command_output("snapm snapset list --json")
        if snapsets:
            try:
                data = json.loads(snapsets)
                test_snapset = next(
                    (
                        s
                        for s in data.get("Snapsets", [])
                        if s["snapset_name"] == self.snapset_name
                    ),
                    None,
                )
                if test_snapset:
                    log_print(
                        f"Final snapset status: {test_snapset.get('snapset_status', 'unknown')}"
                    )
            except json.JSONDecodeError:
                pass

        return True


class FedoraTestStrategy(TestStrategy):
    """Test strategy for Fedora systems"""

    name = "Fedora"

    def get_package_manager(self) -> str:
        return "dnf"


class CentOSTestStrategy(TestStrategy):
    """Test strategy for CentOS Stream systems"""

    name = "CentOS"

    def get_package_manager(self) -> str:
        return "dnf"


class RHELTestStrategy(TestStrategy):
    """Test strategy for RHEL systems"""

    name = "Red Hat Enterprise Linux"

    def get_package_manager(self) -> str:
        return "dnf"


class TestStrategyFactory:
    """Factory for creating OS-specific test strategies"""

    _strategies = {
        "fedora": FedoraTestStrategy,
        "centos": CentOSTestStrategy,
        "rhel": RHELTestStrategy,
    }

    @classmethod
    def create_strategy(cls, os_family: str, vm: SnapmTestVM, vg: str) -> TestStrategy:
        """Create appropriate test strategy for OS family"""

        os_family = os_family.lower()

        # Handle OS variants
        if os_family.startswith("fedora"):
            os_family = "fedora"
        elif "centos" in os_family:
            os_family = "centos"
        elif "rhel" in os_family:
            os_family = "rhel"

        strategy_class = cls._strategies.get(os_family)
        if not strategy_class:
            raise ValueError(f"Unsupported OS family: {os_family}")

        return strategy_class(vm, vg)

    @classmethod
    def list_supported_os(cls) -> List[str]:
        """Return list of supported OS families"""
        return list(cls._strategies.keys())


# Example usage
def run_e2e_test(
    vm_name: str,
    base_os: str = "fedora42",
    storage: str = "lvm",
    uefi: bool = False,
    keep: bool = False,
    repo: str = "snapshotmanager/snapm",
    ref_name: str = "main",
) -> bool:
    """Run end-to-end test for given OS"""
    # Determine volume group name based on OS
    vg_mapping = {"fedora": "fedora", "centos": "cs", "rhel": "rhel"}

    os_key = base_os.lower()
    if "centos" in os_key:
        os_key = "centos"
    elif "rhel" in os_key:
        os_key = "rhel"
    elif os_key.startswith("fedora"):
        os_key = "fedora"

    vg = vg_mapping.get(os_key, "fedora")  # Default to fedora

    log_print(
        f"Preparing test for {vm_name} (base_os={base_os}, storage={storage}, uefi={uefi}, repo={repo}, ref_name={ref_name})"
    )
    # Set up VM
    vm = setup_test_vm(
        vm_name, base_os, storage, uefi=uefi, repo=repo, ref_name=ref_name
    )
    if not vm:
        return False

    success = False

    try:
        # Create test strategy
        strategy = TestStrategyFactory.create_strategy(base_os, vm, vg)

        # Execute tests
        success = strategy.execute_tests()

        if success:
            for step in strategy.get_test_results():
                log_print(f"⭐ {step} ✅")
            log_print(f"🎉 End-to-end test passed for {base_os}")
        else:
            err_print(f"❌ End-to-end test failed for {base_os}")
            err_print(f"Test results: {strategy.get_test_results()}")

        return success

    finally:
        if not keep:
            # Clean up VM
            vm.cleanup()
        else:
            log_print(f"VM running and accessible at root@{vm.ip_address}")
