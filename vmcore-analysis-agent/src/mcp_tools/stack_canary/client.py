#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from typing import Any, List, Optional

from langchain_mcp_adapters.client import MultiServerMCPClient

from src.utils.logging import logger

MCP_SERVER_NAME = "stack_canary"

canary_client = MultiServerMCPClient(
    {
        MCP_SERVER_NAME: {
            "command": sys.executable,
            "args": ["-m", "src.mcp_tools.stack_canary.server"],
            "transport": "stdio",
        }
    }
)
MCP_CLIENT = canary_client

_canary_tools: Optional[List] = None


async def _initialize_tools():
    global _canary_tools
    if _canary_tools is not None:
        return _canary_tools

    try:
        logger.info("Initializing stack_canary MCP tools...")
        _canary_tools = await canary_client.get_tools()

        logger.info(
            "Successfully initialized %d stack_canary tools.\n\t%s",
            len(_canary_tools),
            "\n\t".join(f"{tool.name}: {tool.description}" for tool in _canary_tools),
        )
        return _canary_tools
    except Exception as exc:
        logger.error(f"Failed to initialize stack_canary tools: {exc}")
        _canary_tools = []
        return []


async def initialize_tools() -> List:
    return await _initialize_tools()


def build_tool_payload(
    tool_name: str, raw_args: Any, state: dict[str, Any]
) -> dict[str, Any]:
    if tool_name not in {
        "resolve_stack_canary_slot",
        "classify_saved_rip_frames_tool",
    }:
        raise ValueError(f"Unsupported stack_canary tool: {tool_name}")

    if isinstance(raw_args, dict):
        command = str(raw_args.get("command", "")).strip()
    else:
        command = str(raw_args).strip()

    if not command:
        if tool_name == "resolve_stack_canary_slot":
            raise ValueError(
                "resolve_stack_canary_slot requires a non-empty command string."
            )
        command = ""

    return {
        "command": command,
        "vmcore_path": state["vmcore_path"],
        "vmlinux_path": state["vmlinux_path"],
    }


canary_tools = []
