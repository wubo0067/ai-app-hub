#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# server.py - Crash MCP 服务器实现
# Author: CalmWU
# Created: 2026-01-09

"""
Crash MCP 服务器实现

此模块使用 FastMCP 框架创建一个 MCP 服务器，提供 crash 工具的各种子命令。
每个 crash 子命令都被注册为一个独立的 MCP 工具。
"""

import os
from typing import Annotated
from pydantic import Field
from fastmcp import FastMCP
from .executor import run_crash_command, run_crash_script
from .scsishow import run_scsishow

crash_server = FastMCP(
    "crash_server",
    instructions="This server provides crash analysis tools for Linux vmcore files.",
)


@crash_server.tool()
def scsishow(
    vmcore_path: Annotated[
        str, Field(description="The absolute path to the vmcore file.")
    ],
    vmlinux_path: Annotated[
        str, Field(description="The absolute path to the vmlinux file.")
    ],
    kver: Annotated[
        str, Field(description="The target kernel version (e.g., 4.18.0).")
    ],
) -> str:
    """
    Run scsishow crash subcommand to extract SCSI subsystem properties across 3.10, 4.18, 5.14.
    """
    try:
        return run_scsishow(vmcore_path, vmlinux_path, kver)
    except Exception as e:
        return f"Failed to execute scsishow: {str(e)}"


@crash_server.tool()
def run_script(
    script: Annotated[
        str,
        Field(
            description=(
                "The full crash script content to execute. Commands separated by newlines.\n"
                "Syntax: command1\\ncommand2\\n...\n"
                "Example: mod -s rcu_stall_mod /path/to/module.ko\\ndis -s rcu_stall_thread\\nbt"
            )
        ),
    ],
    vmcore_path: Annotated[
        str, Field(description="The absolute path to the vmcore file.")
    ],
    vmlinux_path: Annotated[
        str, Field(description="The absolute path to the vmlinux file.")
    ],
) -> str:
    """
    Execute multiple crash commands in a single session.
    Useful for executing a sequence of commands where state (like loaded modules) needs to be preserved.
    """
    try:
        return run_crash_script(script, vmcore_path, vmlinux_path, True)
    except Exception as e:
        return f"Failed to execute crash script: {str(e)}"


# 动态注册 crash 子命令工具
def register_crash_tool(name: str, syntax: str, example: str, summary: str):
    """
    动态创建一个 crash 工具并注册到 crash_server
    """

    async def crash_tool_func(
        command: Annotated[
            str,
            Field(
                description=(
                    f"The full '{name}' command string to execute.\n"
                    f"Syntax: {syntax}\n"
                    f"Example: {example}\n"
                    "IMPORTANT: Replace placeholders with actual values and do not include brackets '[]'."
                ),
            ),
        ],
        vmcore_path: Annotated[
            str, Field(description="The absolute path to the vmcore file.")
        ],
        vmlinux_path: Annotated[
            str, Field(description="The absolute path to the vmlinux file.")
        ],
    ) -> str:
        # 统一的执行逻辑
        try:
            output = run_crash_command(command, vmcore_path, vmlinux_path, True)
            return output
        except Exception as e:
            return f"Failed to execute crash command: {str(e)}"

    # 修改文档字符串，FastMCP 会将其作为工具描述
    crash_tool_func.__doc__ = f"{summary} (Crash Subcommand: {name})"
    # 修改函数名以防冲突
    crash_tool_func.__name__ = f"crash_{name}"

    # 手动调用 tool 装饰器进行注册，确保 __doc__ 已经被修改
    crash_server.tool(name=name)(crash_tool_func)
    return crash_tool_func


# 统一格式：(name, syntax, example, summary)
commands = [
    (
        "sys",
        "sys [-c [name|number]] [-t] [-i] config",
        "sys -i",
        "Display system profile and kernel info.",
    ),
    (
        "bt",
        "bt [-a] [-p] [stack_pointer] [pid|task]",
        "bt -a",
        "Display task backtrace.",
    ),
    ("ps", "ps [-k|-u|-G] [-f] [-t] [pid|task]", "ps -ef", "Display process status."),
    (
        "vm",
        "vm [-p|-m|-v|-a|-f] [task|address]",
        "vm -a",
        "Display task virtual memory.",
    ),
    ("files", "files [-d] [task|address]", "files -a", "Display task open files."),
    (
        "kmem",
        "kmem [-f|-F|-c|-C|-i|-s|-S|-v|-V] [address|symbol]",
        "kmem -i",
        "Display kernel memory usage.",
    ),
    (
        "struct",
        "struct struct_name[.member[,member]][-o][-l offset][-rfuxdp] [address | symbol][:cpuspec] [count | -c count]",
        "struct task_struct 0xffff8800",
        "Display kernel structure.",
    ),
    (
        "dis",
        "dis [-rfludxs][-b [num]] [address | symbol] [count]",
        "dis -l jiffies",
        "Disassemble instructions.",
    ),
    (
        "runq",
        "runq [-t] [-T] [-m] [-g] [-c cpu(s)]",
        "runq -t",
        "Display CPU run queues.",
    ),
    ("log", "log [-Ttdmasc]", "log -t", "Display kernel log."),
    (
        "list",
        "list [[-o] offset][-e end][-[s|S] struct[.member[,member] [-l offset]] -[x|d]] [-r|-B] [-h [-O head_offset]|-H] start",
        "list task_struct.p_pptr c169a000",
        "Dump linked list contents.",
    ),
    (
        "ipcs",
        "ipcs [-smMq] [-n pid|task] [id | addr]",
        "ipcs",
        "Display System V IPC info.",
    ),
    (
        "waitq",
        "waitq [ symbol ] | [ struct.member struct_addr ] | [ address ]",
        "waitq task_struct.wait_chldexit c5496000",
        "Display tasks on a wait queue.",
    ),
    (
        "whatis",
        "whatis [[-o] [struct | union | typedef | symbol]] | [[-r [size|range]] [-m member]]",
        "whatis do_fork",
        "Display the definition of structures, unions, typedefs or text/data symbols.",
    ),
    (
        "pte",
        "pte contents ...",
        "pte 13f600",
        "Translate PTE contents to physical address and page bits.",
    ),
    (
        "search",
        "search [-s start] [ -[kKV] | -u | -p | -t | -T ] [-e end | -l length] [-m mask] \
         [-x count] -[cwh] [value | (expression) | symbol | string] ...",
        "search -s _etext -e _edata c2d400eb",
        "This command searches for a given value within a range of user virtual, kernel  virtual, or physical memory space.",
    ),
    (
        "mount",
        "mount [-f][-i] [-n pid|task] [mount|vfsmount|superblock|dev|dir|dentry|inode]",
        "mount -i",
        "Display basic information about the currently-mounted filesystems.",
    ),
    (
        "p",
        "p [-x|-d][-u] [expression | symbol[:cpuspec]]",
        "p jiffies, px jiffies, pd jiffies",
        'This command passes its arguments on to gdb "print" command for evaluation',
    ),
    (
        "dev",
        "dev [-i | -p | -d | -D ] [-V | -v index [file]]",
        "dev -i",
        "If no argument is entered, this command dumps character and block device data",
    ),
    (
        "foreach",
        "foreach [[pid | taskp | name | state | [kernel | user | gleader]] ...] command [flag] [argument]",
        "foreach bt",
        "This command allows for an examination of various kernel data associated with any, or all, tasks in the system, without having to set the context to each targeted task",
    ),
    (
        "timer",
        "timer [-r][-C cpu]",
        "timer -r",
        "This command displays the timer queue entries",
    ),
    (
        "swap",
        "swap",
        "swap",
        "This command displays information for each configured swap device.",
    ),
    (
        "sig",
        "sig [[-l] | [-s sigset]] | [-g] [pid | taskp] ...",
        "sig -g 2578",
        "This command displays signal-handling data of one or more tasks.",
    ),
    (
        "mach",
        "mach [-m | -c -[xd] | -o]",
        "mach",
        "This command displays data specific to a machine type.",
    ),
    (
        "mod",
        "mod -s module [objfile] | -d module | -S [directory] [-D|-t|-r|-R|-o|-g]",
        "mod -s dm_mod",
        "module information and loading of symbols and debugging data",
    ),
    (
        "rd",
        "rd [-adDsSupxmfNR][-8|-16|-32|-64][-o offs][-e addr][-r file][address|symbol] [count]",
        "rd -a linux_banner",
        "This command displays the contents of memory, with the output formatted in several different manners",
    ),
    (
        "vtop",
        "vtop [-c [pid | taskp]] [-u|-k] address ...",
        "vtop c806e000",
        "This command translates a user or kernel virtual address to its physical address.",
    ),
    (
        "set",
        "set [[-a] [pid | taskp] | [-c cpu] | -p] | [crash_variable [setting]] | -v",
        "set -p; set c2fe8000",
        "This command either sets a new context, or gets the current context for display.",
    ),
    (
        "sym",
        "sym [-l] | [-M] | [-m module] | [-p|-n] | [-q string] | [symbol | vaddr]",
        "sym jiffies; sym c0109944",
        "This command translates a symbol to its virtual address, or a static kernel virtual address to its symbol -- or to a symbol-plus-offset value.",
    ),
    (
        "task",
        "task [-R member[,member]] [-dx] [pid | taskp] ...; task -R se.on_rq",
        "task -x; task -R ngroups,groups 2958",
        "This command dumps a formatted display of the contents of a task's task_struct and thread_info structures.",
    ),
    (
        "ptov",
        "ptov [address | offset:cpuspec]",
        "ptov 56e000; ptov b0c0:a",
        "This command translates a hexadecimal physical address into a kernel virtual address.",
    ),
    (
        "ptob",
        "ptob page_number ...",
        "ptob 512a",
        "This command translates a page frame number to its byte value.",
    ),
    (
        "irq",
        "irq [[[index ...] | -u ] | -d | -b | -a | -s [-c cpu]]",
        "irq -d; irq 21",
        "This command collaborates the data in an irq_desc_t, along with its associated hw_interrupt_type and irqaction structure data, into a consolidated per-IRQ display.",
    ),
    (
        "sbitmapq",
        "sbitmapq [-s struct[.member[,member]] -a address [-p] [-v]] -[x|d] address",
        "sbitmapq -s iscsi_cmd -a 0xc0000000671c0000 -v c0000000e118c808",
        "The command dumps the contents of the sbitmap_queue structure and the used bits in the bitmap.",
    ),
    (
        "cifsshow",
        "cifsshow [-h]",
        "cifsshow",
        "Print information about cifs mounts.",
    ),
    (
        "cpuinfo",
        "cpuinfo [-h]",
        "cpuinfo",
        "Print information about the physical CPUs (processors) in a system.",
    ),
    (
        "detailedsearch",
        "detailedsearch [-h] [-m MASK] [-s START] [-l LENGTH] [-k] [-K] [-u] [-c] [-w] [--slab-only] params",
        "detailedsearch ffff8addd2b92700",
        "Search for a value and print matching location details (slab object, task stack, etc.), enhanced version of 'search'.",
    ),
    (
        "dmshow",
        "dmshow [-h]",
        "dmshow",
        "Display information about multipath devices and LVM volumes from vmcore dumps.",
    ),
    (
        "epython",
        "epython program.py arg ...",
        "epython xportshow.py --help",
        "Invoke the embedded Python interpreter to run crash extension scripts.",
    ),
    (
        "fregs",
        "fregs [-h]",
        "fregs",
        "Decode and print subroutine registers and arguments from stack frames.",
    ),
    (
        "hanginfo",
        "hanginfo [-h]",
        "hanginfo",
        "Print information about UNINTERRUPTIBLE threads and categorize them by mutex/semaphore waits.",
    ),
    (
        "keyringshow",
        "keyringshow [-h] (-k KEYRING | -p PID | -a)",
        "keyringshow -a",
        "Print information about keyrings for a specific task or all tasks.",
    ),
    (
        "lsdentry",
        "lsdentry [-h] [-R] [-l] [--params FIELDS] [-x] [--negative] [--partial] dentry",
        "lsdentry /var/log",
        "Print directory information based on the dentry tree, similar to 'ls -l'.",
    ),
    (
        "mdadm",
        "mdadm [-h] [-m] [-d]",
        "mdadm -d",
        "Print information about MD devices (Linux Software RAID).",
    ),
    (
        "modinfo",
        "modinfo [-h] [--disasm=DISASM_MODULE] [--details=MODULE_DETAIL] [-t] [-g] [-a] [-u]",
        "modinfo -t",
        "Print information about DLKMs (dynamically loaded kernel modules).",
    ),
    (
        "nbdshow",
        "nbdshow [-h]",
        "nbdshow",
        "Display information about NBD (Network Block Device) devices and diagnose common issues.",
    ),
    (
        "nfsshow",
        "nfsshow [-h]",
        "nfsshow",
        "Print information about NFS subsystem (both client and server).",
    ),
    (
        "nvme",
        "nvme [-h] [-l [NS]] [-c [CTRL]] [-n [NS]] [-d [CTRL]] [-q [CTRL]] [-i [QID]] [-s [SUB]] [-k]",
        "nvme -k",
        "Print information about NVMe devices, controllers, queues, and check for common NVMe issues.",
    ),
    (
        "pstree",
        "pstree [-p] [-g] [-s] [-t]",
        "pstree -p",
        "Print process list in tree format.",
    ),
    (
        "rqlist",
        "rqlist [-h] [-q [FIELDS]] [-r [FIELDS]] [--bio [FIELDS]] [--pages [FIELDS]] [-x] [--summary] [--time] [--olderthan OLDERTHAN] [--youngerthan YOUNGERTHAN]",
        "rqlist --summary",
        "Print information about block I/O requests and request queues with state and timing info.",
    ),
    # (
    #     "taskinfo",
    #     "taskinfo [-h]",
    #     "taskinfo",
    #     "Print detailed information about tasks, more detailed than the built-in 'ps' command.",
    # ),
    # (
    #     "tslog",
    #     "tslog [-h]",
    #     "tslog",
    #     "Display kernel log (dmesg) with timestamps converted to real date/time.",
    # ),
    (
        "xportshow",
        "xportshow [-h] [--summary] [-iv] [-a] [-t] [--everything]",
        "xportshow --summary",
        "Display networking information including connections summary, interfaces, and TCP/UDP state.",
    ),
]

# 批量注册
for cmd_info in commands:
    register_crash_tool(*cmd_info)

if __name__ == "__main__":
    crash_server.run(transport="stdio", show_banner=False)
