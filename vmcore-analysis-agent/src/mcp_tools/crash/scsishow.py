#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# scsishow.py - implementation of scsishow crash subcommand
# Author: AI Agent

import re
from .executor import run_crash_command, run_crash_script


def _get_logger():
    try:
        from src.utils.logging import logger

        return logger
    except ImportError:
        import logging

        return logging.getLogger(__name__)


def parse_kernel_version(sys_output: str) -> str:
    """Extract release version from sys command."""
    for line in sys_output.splitlines():
        if line.strip().startswith("RELEASE:"):
            return line.split(":")[1].strip()
    return ""


def run_scsishow(vmcore_path: str, vmlinux_path: str, kver: str) -> str:
    """Implement scsishow command for various kernel versions (3.10.0, 4.18.0, 5.14.0)."""
    logger = _get_logger()

    # ---- helper: extract hex address from "eval" output ----
    def _parse_eval_hex(line: str) -> str | None:
        m = re.match(r"^hexadecimal:\s*([0-9a-fA-F]+)", line)
        if m:
            return "0x" + m.group(1).lower()
        return None

    # ================================================================
    # Step 0: consume kernel version
    # ================================================================
    logger.info("Target Kernel Version provided: %s", kver)
    is_kver_4_18 = "4.18" in kver

    # ================================================================
    # Step 1: parse Scsi_Host layout
    # ================================================================
    out_init = run_crash_script(
        "struct -o Scsi_Host\n", vmcore_path, vmlinux_path, True
    )

    scsi_host_size = 0
    dev_offset = 0
    for line in out_init.splitlines():
        m = re.search(r"(?:SIZE|size):\s*(\d+)", line)
        if m:
            scsi_host_size = int(m.group(1))
        m = re.search(r"\[(\d+)\]\s+struct\s+list_head\s+__devices", line)
        if m:
            dev_offset = int(m.group(1))

    # ================================================================
    # Step 2: collect Scsi_Host addresses
    # ================================================================
    shost_addrs: set[str] = set()

    # --- 2a. IDR / IDA tree lookup (fails on 4.18, works on 3.10 & 5.14) ---
    if not is_kver_4_18:
        tree_script = (
            "tree -t radix host_index_idr.idr_rt\n"
            "tree -t xarray host_index_idr.idr_rt\n"
            "tree -t xarray host_index_idr\n"
            "tree -t radix host_index_ida\n"
        )
        out_tree = run_crash_script(tree_script, vmcore_path, vmlinux_path, True)
        for line in out_tree.splitlines():
            m = re.search(r"\[\d+\]\s+(ff[0-9a-f]{10,14})", line.lower())
            if m:
                shost_addrs.add("0x" + m.group(1))
    else:
        logger.info("Skip IDR tree scan on %s; using class-device fallback first", kver)

    # --- 2b. fallback: walk the shost_class subsystem klist ---
    if not shost_addrs:
        # First find class.p and shost_class symbol address
        out_klist1 = run_crash_script(
            "struct class.p scsi_host_class\n"
            "struct class.p shost_class\n"
            "eval shost_class\n",
            vmcore_path,
            vmlinux_path,
            True,
        )

        class_p: str | None = None
        shost_class_addr: str | None = None
        for line in out_klist1.splitlines():
            m = re.search(r"p\s*=\s*(0x[0-9a-fA-F]+)", line)
            if m:
                class_p = m.group(1)
            marker = _parse_eval_hex(line)
            if marker:
                shost_class_addr = marker

        if class_p:
            # Then get all needed struct offsets in one script call
            out_osscan = run_crash_script(
                "struct -o subsys_private\n"
                "struct -o klist\n"
                "struct -o klist_node\n"
                "struct -o device\n"
                "struct -o Scsi_Host\n",
                vmcore_path,
                vmlinux_path,
                True,
            )

            klist_devices_off: int | None = None
            klist_list_off: int | None = None
            knode_class_off: int | None = None
            shost_dev_off: int | None = None
            for line in out_osscan.splitlines():
                m = re.search(r"\[(\d+)\]\s+struct\s+klist\s+klist_devices", line)
                if m:
                    klist_devices_off = int(m.group(1))

                m = re.search(r"\[(\d+)\]\s+struct\s+list_head\s+k_list", line)
                if m:
                    klist_list_off = int(m.group(1))

                m = re.search(r"\[(\d+)\]\s+struct\s+klist_node\s+knode_class", line)
                if m:
                    knode_class_off = int(m.group(1))

                m = re.search(r"\[(\d+)\]\s+struct\s+device\s+shost_dev\b", line)
                if m:
                    shost_dev_off = int(m.group(1))

            logger.info(
                "Fallback offsets: class_p=%s klist_devices=%s k_list=%s "
                "knode_class=%s shost_dev=%s",
                class_p,
                klist_devices_off,
                klist_list_off,
                knode_class_off,
                shost_dev_off,
            )

            if all(
                v is not None
                for v in (
                    klist_devices_off,
                    klist_list_off,
                    knode_class_off,
                    shost_dev_off,
                )
            ):
                klist_head = hex(
                    int(class_p, 16) + klist_devices_off + klist_list_off  # type: ignore[operator]
                )
                out_klist2 = run_crash_script(
                    f"list klist_node.n_node -s klist_node.n_klist -H {klist_head}\n",
                    vmcore_path,
                    vmlinux_path,
                    True,
                )

                device_addrs: set[str] = set()
                for line in out_klist2.splitlines():
                    m = re.search(r"^(ff[0-9a-f]{10,14})", line.lower())
                    if m:
                        node_addr = int(m.group(1), 16)
                        # "list klist_node.n_node" already subtracts n_node
                        # offset → the printed addresses are klist_node struct
                        # addresses.  klist_node lives at knode_class inside
                        # struct device, so device = klist_node - knode_class.
                        device_addrs.add(
                            hex(node_addr - knode_class_off)  # type: ignore[operator]
                        )

                if device_addrs and shost_class_addr:
                    eval_scr = ""
                    for d_addr in sorted(device_addrs):
                        eval_scr += f"eval {d_addr}\n"
                        eval_scr += f"struct device.class -x {d_addr}\n"

                    out_devptr = run_crash_script(
                        eval_scr, vmcore_path, vmlinux_path, True
                    )
                    curr_dev: str | None = None
                    for line in out_devptr.splitlines():
                        marker = _parse_eval_hex(line)
                        if marker:
                            curr_dev = marker
                            continue

                        m = re.search(r"class\s*=\s*(0x[0-9a-fA-F]+)", line.lower())
                        if m and curr_dev:
                            class_addr = m.group(1).lower()
                            if class_addr == shost_class_addr.lower():
                                shost_addr = (
                                    int(curr_dev, 16) - shost_dev_off  # type: ignore[operator]
                                )
                                if shost_addr > 0:
                                    shost_addrs.add(hex(shost_addr))

    if not shost_addrs:
        return "crash> scsishow -d\n\nNo SCSI hosts found in memory."

    # ================================================================
    # Step 3: query host metadata and device list
    # ================================================================
    script3 = ""
    for h in shost_addrs:
        script3 += f"eval {h}\n"
        script3 += f"struct Scsi_Host.host_no,hostt,shost_data -x {h}\n"
        dev_list_addr = hex(int(h, 16) + dev_offset)
        script3 += f"list scsi_device.siblings -H {dev_list_addr}\n"

    out_script3 = run_crash_script(script3, vmcore_path, vmlinux_path, True)

    shosts: dict[str, dict] = {}
    curr_h: str | None = None

    for line in out_script3.splitlines():
        marker = _parse_eval_hex(line)
        if marker:
            curr_h = marker
            if curr_h in shost_addrs:
                shosts[curr_h] = {"devices": []}
            continue

        if curr_h and curr_h in shosts:
            for f in ("host_no", "hostt", "shost_data"):
                m = re.search(rf"{f}\s*=\s*(0x[0-9a-fA-F]+|-?\d+)", line)
                if m:
                    shosts[curr_h][f] = m.group(1)

            m = re.match(r"^(ff[0-9a-f]{10,14})", line.lower())
            if m:
                shosts[curr_h]["devices"].append("0x" + m.group(1))

    # ================================================================
    # Step 4: query driver names and device details
    # ================================================================
    script4 = ""
    all_hostts = {v.get("hostt") for v in shosts.values() if v.get("hostt")}
    all_devs = {d for v in shosts.values() for d in v.get("devices", [])}

    for ht in all_hostts:
        script4 += f"eval {ht}\n"
        script4 += f"struct scsi_host_template.proc_name {ht}\n"

    for d in all_devs:
        script4 += (
            f"eval {d}\n"
            f"struct scsi_device.id,channel,lun,vendor,model,sdev_state,type -x {d}\n"
            f"struct scsi_device.iorequest_cnt,iodone_cnt,ioerr_cnt -x {d}\n"
        )

    out_script4 = run_crash_script(script4, vmcore_path, vmlinux_path, True)

    hostt_names: dict[str, str] = {}
    devices: dict[str, dict] = {}
    curr_target: str | None = None
    curr_counter_field: str | None = None  # tracks multi-line atomic_t parsing

    for line in out_script4.splitlines():
        marker = _parse_eval_hex(line)
        if marker:
            curr_target = marker
            curr_counter_field = None
            if curr_target in all_devs:
                devices[curr_target] = {}
            continue

        if curr_target in all_hostts:
            m = re.search(
                r'proc_name\s*=\s*0x[0-9a-fA-F]+\s+(?:<[^>]+>\s+)?"([^"]+)"', line
            )
            if m:
                hostt_names[curr_target] = m.group(1)

        if curr_target in all_devs:
            # Handle multi-line atomic_t: waiting for "counter = N" on next line(s)
            if curr_counter_field:
                mc = re.search(r"\bcounter\s*=\s*(0x[0-9a-fA-F]+|-?\d+)", line)
                if mc:
                    devices[curr_target][curr_counter_field] = int(mc.group(1), 0)
                    curr_counter_field = None
                elif "}" in line:
                    curr_counter_field = None
                continue

            for f in ("id", "channel", "lun", "type"):
                m = re.search(rf"\b{f}\s*=\s*(0x[0-9a-fA-F]+|-?\d+)", line)
                if m:
                    devices[curr_target][f] = int(m.group(1), 0)

            m = re.search(r"\bsdev_state\s*=\s*([A-Z0-9_]+)", line)
            if m:
                devices[curr_target]["sdev_state"] = m.group(1)

            for f in ("vendor", "model"):
                # Handle optional hex address prefix: vendor = 0xff... "VALUE"
                m = re.search(rf'\b{f}\s*=\s*(?:0x[0-9a-fA-F]+\s+)?"([^"]*)"', line)
                if m:
                    devices[curr_target][f] = m.group(1).strip()

            for f in ("iorequest_cnt", "iodone_cnt", "ioerr_cnt"):
                # Single-line atomic_t: iorequest_cnt = {counter = 22} or {counter = 0x16}
                m = re.search(
                    rf"\b{f}\s*=\s*\{{\s*counter\s*=\s*(0x[0-9a-fA-F]+|-?\d+)\s*\}}",
                    line,
                )
                if m:
                    devices[curr_target][f] = int(m.group(1), 0)
                    break
                # Start of multi-line block: iorequest_cnt = {
                m = re.search(rf"\b{f}\s*=\s*\{{", line)
                if m:
                    curr_counter_field = f
                    # Counter might also be on the same line (edge case)
                    mc = re.search(r"\bcounter\s*=\s*(0x[0-9a-fA-F]+|-?\d+)", line)
                    if mc:
                        devices[curr_target][f] = int(mc.group(1), 0)
                        curr_counter_field = None
                    break
                # Plain integer fallback (non-atomic_t kernels): iorequest_cnt = 22
                m = re.search(rf"\b{f}\s*=\s*(0x[0-9a-fA-F]+|-?\d+)", line)
                if m:
                    devices[curr_target][f] = int(m.group(1), 0)
                    break

    # ================================================================
    # Output formatting
    # ================================================================
    DEV_TYPE_MAP = {
        0: "Direct-Access",
        1: "Tape",
        2: "Printer",
        3: "Processor",
        4: "WORM",
        5: "CD-ROM",
        6: "Scanner",
        7: "Optical",
        8: "Medium Ch",
        9: "Comms",
        12: "Storage",
        13: "Enclosure",
        14: "RBC",
    }

    lines: list[str] = []
    lines.append("crash> scsishow -d")
    lines.append(" ")
    lines.append("=" * 136)
    lines.append("HOST      DRIVER")
    lines.append(
        "NAME      NAME                   Scsi_Host                "
        "shost_data               hostdata                "
    )
    lines.append("-" * 99)

    sorted_hosts = sorted(
        shosts.items(), key=lambda x: int(x[1].get("host_no", "0"), 0)
    )
    for h_addr, h_info in sorted_hosts:
        host_no = int(h_info.get("host_no", "0"), 0)
        hname = f"host{host_no}"
        hostt = h_info.get("hostt")
        driver_name = hostt_names.get(hostt, "<unknown>")  # type: ignore[arg-type]
        shost_data = h_info.get("shost_data", "0x0").lstrip("0x").rjust(16, "0")
        haddr_clean = h_addr.lstrip("0x").rjust(16, "0")
        hostdata = (
            hex(int(h_addr, 16) + scsi_host_size)[2:].rjust(16, "0")
            if scsi_host_size
            else "0000000000000000"
        )
        lines.append(
            f"{hname:<9s} {driver_name:<22s} {haddr_clean}         "
            f"{shost_data}         {hostdata}"
        )

    lines.append("")
    lines.append(
        "DEV NAME     scsi_device         H:C:T:L      VENDOR/MODEL               "
        "DEVICE STATE           IOREQ-CNT  IODONE-CNT          IOERR-CNT"
    )
    lines.append("-" * 136)

    for h_addr, h_info in sorted_hosts:
        host_no = int(h_info.get("host_no", "0"), 0)
        for d_addr in h_info.get("devices", []):
            d_info = devices.get(d_addr, {})
            channel = d_info.get("channel", 0)
            id_ = d_info.get("id", 0)
            lun = d_info.get("lun", 0)
            hctl = f"{host_no}:{channel}:{id_}:{lun}"

            typ = d_info.get("type", 0)
            dev_name = DEV_TYPE_MAP.get(typ, f"Type {typ}")

            daddr_clean = d_addr.lstrip("0x").rjust(16, "0")

            # Crash struct output sometimes doesn't null-terminate character arrays, causing
            # vendor string to bleed into model. We manually truncate to their expected lengths.
            vendor_str = d_info.get("vendor", "")[:8].strip()
            model_str = d_info.get("model", "")[:16].strip()
            vendor = f"{vendor_str} {model_str}".strip()

            state = d_info.get("sdev_state", "SDEV_RUNNING")
            req = d_info.get("iorequest_cnt", 0)
            done = d_info.get("iodone_cnt", 0)
            err = d_info.get("ioerr_cnt", 0)

            lines.append(
                f"{dev_name:<12s} {daddr_clean}    {hctl:<12s} {vendor:<26s} "
                f"{state:<22s} {req:>8}  {done:>8}  (  0)       {err:>4}"
            )

    return "\n".join(lines)
