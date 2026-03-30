#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# executor.py - Crash 工具执行器实现
# Author: CalmWU
# Created: 2026-01-09

import subprocess
from src.utils.logging import logger
from src.utils.os import get_linux_distro_version

distro, version = get_linux_distro_version()

# crash 命令单次执行的最长等待时间（秒）。
# log | grep 等大输出命令可能导致无限期阻塞，超时后强制终止进程并返回截断结果。
COMMAND_TIMEOUT = 120

# 单次命令返回的最大字符数。超出部分将被截断以防止 LLM token 溢出。
MAX_OUTPUT_CHARS = 100_000

CRASH_IGNORE_MARKERS = [
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
    "you are welcome",
    "certain conditions",
    "There is NO WARRANTY",
    "Find the GDB manual",
    "www.gnu.org",
]


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
    try:
        stdout, stderr = process.communicate(
            input=command + "\nquit\n", timeout=COMMAND_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, _ = process.communicate()
        logger.warning(
            f"Crash command '{command}' timed out after {COMMAND_TIMEOUT}s. "
            "Partial output will be returned."
        )
        return (
            f"[TIMEOUT] Command '{command}' exceeded {COMMAND_TIMEOUT}s limit "
            "and was terminated. The command generates too much output or takes too long. "
            "Use a more targeted command or filter the output with grep."
        )

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
        if any(keyword in line for keyword in CRASH_IGNORE_MARKERS):
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

    if len(output) > MAX_OUTPUT_CHARS:
        logger.warning(
            f"Command '{command}' output truncated: {len(output)} -> {MAX_OUTPUT_CHARS} chars"
        )
        output = (
            output[:MAX_OUTPUT_CHARS]
            + f"\n\n[OUTPUT TRUNCATED: {len(output)} chars total, showing first {MAX_OUTPUT_CHARS}. "
            "Use a more specific command or grep filter to reduce output.]"
        )

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

    try:
        stdout, stderr = process.communicate(input=input_str, timeout=COMMAND_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        logger.warning(
            f"Crash script timed out after {COMMAND_TIMEOUT}s. Script content: {script_content[:200]!r}"
        )
        return (
            f"[TIMEOUT] Script execution exceeded {COMMAND_TIMEOUT}s limit and was terminated. "
            "One or more commands in the script generates too much output or takes too long. "
            "Split the script into smaller steps and avoid large-output commands like 'log' without filters."
        )

    if process.returncode != 0:
        err = f"Crash script failed with error:\n{stderr}"
        logger.error(err)
        raise Exception(err)

    lines = stdout.splitlines()
    filtered_lines = []

    # 额外的 ignore markers
    extra_ignore_markers = [
        "KERNEL:",
        "DUMPFILE:",
        "CPUS:",
        "DATE:",
        "UPTIME:",
        "LOAD AVERAGE:",
        "MEMORY:",
        "NODENAME:",
        "SMP:",
        "TASKS:",
        "RELEASE:",
        "VERSION:",
        "MACHINE",
        "PID",
        "PANIC:",
        "COMMAND",
        "TASK:",
        "CPU:",
        "STATE",
    ]
    for line in lines:
        # 过滤包含特定标记的行
        if any(
            marker in line for marker in CRASH_IGNORE_MARKERS + extra_ignore_markers
        ):
            continue

        # 过滤回显的 quit 命令
        if line.strip() == "quit":
            continue

        filtered_lines.append(line)

    output = "\n".join(filtered_lines).strip()

    if len(output) > MAX_OUTPUT_CHARS:
        logger.warning(
            f"Script output truncated: {len(output)} -> {MAX_OUTPUT_CHARS} chars"
        )
        output = (
            output[:MAX_OUTPUT_CHARS]
            + f"\n\n[OUTPUT TRUNCATED: {len(output)} chars total, showing first {MAX_OUTPUT_CHARS}. "
            "Use a more specific command or grep filter to reduce output.]"
        )

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
        return run_crash_script_rhel9(
            script_content, vmcore_path, vmlinux_path, verbose
        )
    else:
        return "functionality for other distros/versions not yet implemented."
