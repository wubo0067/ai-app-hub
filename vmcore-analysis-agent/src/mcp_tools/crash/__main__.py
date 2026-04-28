#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# __main__.py - Crash MCP 工具客户端主入口
# Author: CalmWU
# Created: 2026-01-09

import asyncio
from langchain_mcp_adapters.tools import load_mcp_tools
from src.utils.logging import logger
from .client import crash_client

# def test_rhel9_crash():
#     command = "sys -i"
#     vmcore_path = "/var/crash/127.0.0.1-2026-01-07-16:46:03/vmcore"
#     vmlinux_path = "/usr/lib/debug/lib/modules/5.14.0-611.9.1.el9_7.x86_64/vmlinux"
#     debug = True

#     logger.info("Starting RHEL9 crash test")  # 测试日志输出
#     run_crash_command_rhel9(command, vmcore_path, vmlinux_path, debug)
#     logger.info("Completed RHEL9 crash test")


# python -m src.mcp_tools.crash
async def test_crash_mcp():
    logger.info("Starting crash MCP client")
    tool_names = set()
    result = ""

    test_cmds: dict[str, str] = {"sys": "sys -i", "bt": "bt -c 0"}

    try:
        async with crash_client.session("crash") as session:
            logger.info("Session established")
            tools = await load_mcp_tools(session)
            for tool in tools:
                tool_names.add(tool.name)
                logger.info(f"Loaded tool: {tool.name}")

            for name, command in test_cmds.items():
                sys_tool = next((t for t in tools if t.name == name), None)
                if sys_tool:
                    logger.info(f"Executing '{command}' command using '{name}' tool")
                    result = await sys_tool.ainvoke(
                        {
                            "command": command,
                            "vmcore_path": "/var/crash/127.0.0.1-2026-01-07-16:46:03/vmcore",
                            "vmlinux_path": "/usr/lib/debug/lib/modules/5.14.0-611.9.1.el9_7.x86_64/vmlinux",
                        }
                    )
    except Exception as e:
        logger.error(f"Error establishing session: {e}")
        raise

    logger.info(f"tools: {tool_names}")
    logger.info(f"'sys -i' command result:\n{result}")


async def test_run_script_tool():
    logger.info("Starting run_script tool test")

    script_content = """mod -s rcu_stall_mod /home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko
dis -s rcu_stall_thread
sym rcu_stall_thread
bt"""

    vmcore_path = "/var/crash/127.0.0.1-2026-01-29-15:16:02/vmcore"
    vmlinux_path = "/usr/lib/debug/lib/modules/5.14.0-611.9.1.el9_7.x86_64/vmlinux"

    try:
        async with crash_client.session("crash") as session:
            logger.info("Session established")
            tools = await load_mcp_tools(session)
            # next 从迭代器 tools 中找到 name 为 "run_script" 的工具，如果没有则返回 None
            run_script_tool = next((t for t in tools if t.name == "run_script"), None)
            if run_script_tool:
                logger.info("Executing 'run_script' tool")
                result = await run_script_tool.ainvoke(
                    {
                        "script": script_content,
                        "vmcore_path": vmcore_path,
                        "vmlinux_path": vmlinux_path,
                    }
                )
                logger.info(f"'run_script' result:\n{result}")
            else:
                logger.error("Tool 'run_script' not found")

    except Exception as e:
        logger.error(f"Error testing run_script: {e}")
        raise


async def test_scsishow_tool():
    logger.info("Starting scsishow tool test")

    vmcore_path = "/home/calmwu/Downloads/crash_case/Case_04419769/vmcore"
    vmlinux_path = "/usr/lib/debug/lib/modules/4.18.0-553.22.1.el8_10.x86_64/vmlinux"

    try:
        async with crash_client.session("crash") as session:
            logger.info("Session established")
            tools = await load_mcp_tools(session)
            scsishow_tool = next((t for t in tools if t.name == "scsishow"), None)
            if scsishow_tool:
                logger.info("Executing 'scsishow' tool with kver='4.18.0'")
                result = await scsishow_tool.ainvoke(
                    {
                        "vmcore_path": vmcore_path,
                        "vmlinux_path": vmlinux_path,
                        "kver": "4.18.0",
                    }
                )
                logger.info(f"'scsishow' result:\n{result}")
            else:
                logger.error("Tool 'scsishow' not found")

    except Exception as e:
        logger.error(f"Error testing scsishow: {e}")
        raise


async def main():
    # await test_crash_mcp()
    # await test_run_script_tool()
    await test_scsishow_tool()


if __name__ == "__main__":
    asyncio.run(main())
