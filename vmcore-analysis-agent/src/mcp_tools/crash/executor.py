import subprocess
from src.utils.logging import logger
from src.utils.os import get_linux_distro_version

distro, version = get_linux_distro_version()


def run_crash_command_rhel9(command, vmcore_path, vmlinux_path, verbose=False):
    """Run a crash command on RHEL 9 systems.
    Args:
        command (str): The crash command to run.
        vmcore_path (str): The path to the vmcore file.
        vmlinux_path (str): The path to the vmlinux file.
        debug (bool): Whether to enable debug mode.
    Returns:
        str: The output of the crash command.
    """
    full_cmd = ["crash", vmlinux_path, vmcore_path]
    process = subprocess.Popen(
        full_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1024 * 1024,
    )
    stdout, stderr = process.communicate(input=command + "\nquit\n")

    # if verbose:
    #     logger.info(
    #         "Raw output for '{command}' on RHEL 9:\n{output}".format(
    #             command=command, output=stdout
    #         )
    #     )

    if process.returncode != 0:
        err = "Crash command '{command}' failed with error:\n{error}".format(
            command=command, error=stderr
        )
        logger.error(err)
        raise Exception(err)

    lines = stdout.splitlines()
    filtered_lines = []
    state_found = False
    capture = False

    for line in lines:
        if any(
            keyword in line
            for keyword in [
                "crash ",
                "Copyright",
                "GNU gdb (GDB)",
                "This GDB was configured",
                "Type",
                "For help",
                "please wait...",
                "NOTE: stdin: not a tty",
                "quit",
                "License GPLv3+",
                "This program",
                "show copying",
                "show warranty",
                "free software",
                "no warranty",
            ]
        ):
            continue

        if "STATE:" in line:
            state_found = True
            continue

        if state_found and not line.strip() and not capture:
            capture = True
            continue

        if capture:
            filtered_lines.append(line)

    output = "\n".join(filtered_lines).strip()

    if verbose:
        logger.info(f"Filtered output for '{command}' (RHEL 9):\n{output}")

    if not output:
        logger.warning(f"Warning: No valid output from command '{command}'")

    return output


def run_crash_script_rhel9(script_content, vmcore_path, vmlinux_path, verbose=False):
    """Run multiple crash commands (script) on RHEL 9 systems.

    Args:
        script_content (str): The content of the script (commands separated by newlines).
        vmcore_path (str): The path to the vmcore file.
        vmlinux_path (str): The path to the vmlinux file.
        verbose (bool): Whether to enable verbose mode.
    Returns:
        str: The filtered output of the crash session.
    """
    full_cmd = ["crash", vmlinux_path, vmcore_path]

    # 执行 crash 命令，通过 stdin 传入脚本内容
    process = subprocess.Popen(
        full_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1024 * 1024,
    )

    # 确保脚本以换行符和 quit 结束，以便正常退出 crash
    input_str = script_content
    if not input_str.endswith("\n"):
        input_str += "\n"
    input_str += "quit\n"

    stdout, stderr = process.communicate(input=input_str)

    if process.returncode != 0:
        err = f"Crash script failed with error:\n{stderr}"
        logger.error(err)
        raise Exception(err)

    lines = stdout.splitlines()
    filtered_lines = []

    # 定义需要过滤的 crash 启动横幅和无关信息
    # 这些是 crash 启动时默认打印的版权、GDB 信息等
    ignore_markers = [
        "crash ",
        "Copyright",
        "GNU gdb",
        "This GDB",
        "Type",
        "For help",
        "please wait...",
        "NOTE: stdin: not a tty",
        "License GPL",
        "This program",
        "show copying",
        "show warranty",
        "free software",
        "no warranty",
    ]

    for line in lines:
        # 过滤包含特定标记的行
        if any(marker in line for marker in ignore_markers):
            continue

        # 过滤回显的 quit 命令
        if line.strip() == "quit":
            continue

        filtered_lines.append(line)

    output = "\n".join(filtered_lines).strip()

    if verbose:
        logger.info(f"Filtered output for script:\n{output}")

    return output


def run_crash_command(full_subcmd, vmcore_path, vmlinux_path, verbose=False):
    """Run a crash subcommand on the given vmcore and vmlinux files.
    Args:
        full_subcmd (str): The crash subcommand to run.
        vmcore_path (str): The path to the vmcore file.
        vmlinux_path (str): The path to the vmlinux file.
    Returns:
        str: The output of the crash command.
    """

    # 工具运行的 OS 环境和版本
    if distro == "rhel" and int(version) == 9:
        return run_crash_command_rhel9(full_subcmd, vmcore_path, vmlinux_path, verbose)
    else:
        return "functionality for other distros/versions not yet implemented."


def run_crash_script(script_content, vmcore_path, vmlinux_path, verbose=False):
    """Run a crash script on the given vmcore and vmlinux files.
    Args:
        script_content (str): The content of the script.
        vmcore_path (str): The path to the vmcore file.
        vmlinux_path (str): The path to the vmlinux file.
    Returns:
        str: The output of the crash script.
    """
    if distro == "rhel" and int(version) == 9:
        return run_crash_script_rhel9(script_content, vmcore_path, vmlinux_path, verbose)
    else:
        return "functionality for other distros/versions not yet implemented."
