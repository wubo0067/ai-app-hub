from fastmcp import FastMCP
from .cmd import run_crash_command
from utils.os import get_linux_distro_version
import os

crash_server = FastMCP(
    "crash_server",
    log_level="ERROR",
    instructions="This server provides crash analysis tools.",
)

# 性能优化：预先获取发行版信息，避免每次调用工具时重复执行
DISTRO, VERSION = get_linux_distro_version()


@crash_server.tool()  # 修正：使用正确的实例名
def run_crash_subcommand(cmd: str, vmcore_path: str, vmlinux_path: str) -> str:
    """Run the 'sys' command in crash analysis.

    Args:
       cmd (str): The argument for the sys full command. example: 'sys -i'
       vmcore (str): The absolute path to the vmcore file.
       vmlinux (str): The absolute path to the vmlinux file (with debug symbols).
    Returns:
       str: The output from the crash command or an error message.
    """
    # 路径校验
    if not os.path.exists(vmcore_path):
        return f"Error: vmcore file not found at {vmcore_path}"
    if not os.path.exists(vmlinux_path):
        return f"Error: vmlinux file not found at {vmlinux_path}"

    try:
        # 使用预先获取的 DISTRO 和 VERSION
        output = run_crash_command(
            cmd, vmcore_path, vmlinux_path, DISTRO, VERSION, True
        )
        return output
    except Exception as e:
        return f"Failed to execute crash command: {str(e)}"


if __name__ == "__main__":
    crash_server.run(transport="stdio")
