import os
from pydantic import Field
from fastmcp import FastMCP
from .executor import run_crash_command

crash_server = FastMCP(
    "crash_server",
    log_level="ERROR",
    instructions="This server provides crash analysis tools for Linux vmcore files.",
)


# 动态注册 crash 子命令工具
def register_crash_tool(name: str, syntax: str, example: str, summary: str):
    """
    动态创建一个 crash 工具并注册到 crash_server
    """

    @crash_server.tool(name=name)
    def crash_func(
        command: str = Field(
            ...,
            description=(
                f"The full '{name}' command string to execute.\n"
                f"Syntax: {syntax}\n"
                f"Example: {example}\n"
                "IMPORTANT: Replace placeholders with actual values and do not include brackets '[]'."
            ),
        ),
        vmcore_path: str = Field(
            ..., description="The absolute path to the vmcore file."
        ),
        vmlinux_path: str = Field(
            ..., description="The absolute path to the vmlinux file."
        ),
    ):
        """统一执行逻辑"""
        # 统一的执行逻辑
        try:
            output = run_crash_command(command, vmcore_path, vmlinux_path, True)
            return output
        except Exception as e:
            return f"Failed to execute crash command: {str(e)}"

    # 修改文档字符串，FastMCP 会将其作为工具描述
    crash_func.__doc__ = f"{summary} (Crash Subcommand: {name})"
    # 修改函数名以防冲突（虽然 FastMCP 主要看 tool(name=...)）
    crash_func.__name__ = f"crash_{name}"
    return crash_func


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
        "This command allows for an examination of various kernel data associated \
            with any, or all, tasks in the system, without having to set the context to each targeted task",
    ),
]

# 批量注册
for cmd_info in commands:
    register_crash_tool(*cmd_info)

if __name__ == "__main__":
    crash_server.run(transport="stdio")
