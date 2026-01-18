# Soft Lockup Simulation Module

This kernel module simulates a soft lockup condition by creating a kernel thread that consumes CPU resources without yielding control, triggering the Linux kernel's soft lockup detector.

## Overview

A soft lockup occurs when a process runs in kernel mode for an extended period without yielding control back to the scheduler. This module creates a kernel thread that enters a tight loop, preventing other processes from executing on the affected CPU core.

## Requirements

- Red Hat Enterprise Linux 9.3 or compatible system
- Kernel headers installed (`kernel-devel` package)
- Development tools (`gcc`, `make`)
- Root privileges to load kernel modules

## Building the Module

1. Ensure kernel development packages are installed:
   ```bash
   sudo dnf install kernel-devel kernel-headers gcc make
   ```

## Loading the Kernel Module

To load the kernel module and simulate a soft lockup, follow these steps:

1. Build the kernel module as described in the "Building the Module" section.
2. Load the module using `insmod`:
   ```bash
   sudo insmod soft_lockup_module.ko enable_soft_lockup=1 cpu_number=<CPU_ID>
   ```
   Replace `<CPU_ID>` with the desired CPU core number (or use `-1` to allow any CPU).
3. Verify that the module is loaded:
   ```bash
   lsmod | grep soft_lockup_module
   ```
4. Check the kernel logs to confirm the module is running:
   ```bash
   dmesg | tail
   ```

## Configuring the OS Environment

To ensure the system is configured to detect and generate a vmcore for soft lockup crashes, follow these steps:

1. **Enable kdump**:
   - Install the `kdump` service if not already installed:
     ```bash
     sudo dnf install kexec-tools
     ```
   - Enable and start the `kdump` service:
     ```bash
     sudo systemctl enable kdump
     sudo systemctl start kdump
     ```

2. **Configure Kernel Parameters**:
   - Edit the GRUB configuration to include crashkernel parameters:
     ```bash
     sudo grubby --update-kernel=ALL --args="crashkernel=auto"
     ```
   - Reboot the system to apply changes:
     ```bash
     sudo reboot
     ```

3. **Verify kdump Configuration**:
   - Check the status of the `kdump` service:
     ```bash
     sudo systemctl status kdump
     ```
   - Ensure the crash dump location is configured in `/etc/kdump.conf` (e.g., `path /var/crash`).

## Checking for Soft Lockup Crashes

1. **Trigger the Soft Lockup**:
   - Load the kernel module as described above.
   - The soft lockup detector in the Linux kernel should log warnings in the kernel logs after a few seconds.
   - Check the logs for soft lockup messages:
     ```bash
     dmesg | grep "soft lockup"
     ```

2. **Generate a vmcore**:
   - If the system is configured correctly, a crash dump (vmcore) will be generated when the soft lockup occurs.
   - The vmcore file will be saved in the location specified in `/etc/kdump.conf` (e.g., `/var/crash`).

3. **Analyze the vmcore**:
   - Use `crash` or `gdb` to analyze the vmcore:
     ```bash
     sudo crash /usr/lib/debug/lib/modules/$(uname -r)/vmlinux /var/crash/<vmcore>
     ```

Replace `<vmcore>` with the actual vmcore file name.

## Checking and Configuring Soft Lockup Panic Parameters

To ensure the system triggers a panic on soft lockup, verify and configure the following kernel parameters:

1. **Check Current Kernel Parameters**:
   - Use the following command to check the current values of `kernel.softlockup_panic`:
     ```bash
     sysctl kernel.softlockup_panic
     ```
   - If the value is `0`, the system will not panic on a soft lockup.

2. **Enable Soft Lockup Panic**:
   - To enable panic on soft lockup, set the parameter to `1`:
     ```bash
     sudo sysctl -w kernel.softlockup_panic=1
     ```
   - To make this change persistent across reboots, add the following line to `/etc/sysctl.conf`:
     ```bash
     kernel.softlockup_panic = 1
     ```

3. **Verify the Change**:
   - Reboot the system and verify the parameter is set correctly:
     ```bash
     sysctl kernel.softlockup_panic
     ```

By enabling `kernel.softlockup_panic`, the system will trigger a kernel panic when a soft lockup is detected, allowing the `kdump` service to capture a vmcore for analysis.

## Unloading the Kernel Module

To stop the soft lockup simulation and unload the module:

1. Unload the module using `rmmod`:
   ```bash
   sudo rmmod soft_lockup_module
   ```
2. Verify that the module is unloaded:
   ```bash
   lsmod | grep soft_lockup_module
   ```
3. Check the kernel logs to confirm the module has been unloaded:
   ```bash
   dmesg | tail
   ```