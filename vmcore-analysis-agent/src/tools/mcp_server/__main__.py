from .cmd import run_crash_command_rhel9
from utils.logging import logger


def test_rhel9_crash():
    command = "sys -i"
    vmcore_path = "/var/crash/127.0.0.1-2026-01-07-16:46:03/vmcore"
    vmlinux_path = "/usr/lib/debug/lib/modules/5.14.0-611.9.1.el9_7.x86_64/vmlinux"
    debug = True

    logger.info("Starting RHEL9 crash test")  # 测试日志输出
    run_crash_command_rhel9(command, vmcore_path, vmlinux_path, debug)
    logger.info("Completed RHEL9 crash test")


def main():
    # Example usage of the run_crash_command_rhel9 function
    test_rhel9_crash()


if __name__ == "__main__":
    main()
