#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# client.py - Source Patch MCP 客户端实现
# Author: CalmWU
# Created: 2026-01-28

# src/mcp_tools/source_patch/client.py
import sys
import json
from typing import List, Optional
from langchain_mcp_adapters.client import MultiServerMCPClient
from src.utils.logging import logger

MCP_SERVER_NAME = "source_patch"

# 初始化 MCP 客户端
patch_client = MultiServerMCPClient(
    {
        MCP_SERVER_NAME: {
            "command": sys.executable,
            "args": ["-m", "src.mcp_tools.source_patch.server"],
            "transport": "stdio",
        }
    }
)
MCP_CLIENT = patch_client

# 全局工具列表（延迟初始化）
_patch_tools: Optional[List] = None


async def _initialize_tools():
    """异步初始化工具列表"""
    global _patch_tools
    if _patch_tools is not None:
        return _patch_tools

    try:
        logger.info("Initializing source_patch MCP tools...")
        _patch_tools = await patch_client.get_tools()
        # 详细打印 tool 信息
        logger.info(
            f"Successfully initialized {len(_patch_tools)} source_patch tools.\n\t%s",
            "\n\t".join(f"{tool.name}: {tool.description}" for tool in _patch_tools),
        )
        return _patch_tools
    except Exception as e:
        logger.error(f"Failed to get tools from MCP client: {e}")
        _patch_tools = []
        return []


async def initialize_patch_tools() -> List:
    """
    异步初始化 source_patch 工具（推荐在 async 上下文中使用）

    Returns:
        List: source_patch 工具列表
    """
    return await _initialize_tools()


async def initialize_tools() -> List:
    return await initialize_patch_tools()


def build_tool_payload(
    tool_name: str, raw_args, state: dict[str, object]
) -> dict[str, object]:
    command = (
        raw_args.get("command", "") if isinstance(raw_args, dict) else str(raw_args)
    )
    if not command:
        raise ValueError(
            f"{tool_name} requires a JSON object string in action.arguments."
        )

    try:
        payload = json.loads(command)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{tool_name} requires action.arguments to encode a JSON object string: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(f"{tool_name} requires a JSON object payload.")
    return payload


# 模块级别的工具列表（可能为空，需要在运行时初始化）
patch_tools = []
