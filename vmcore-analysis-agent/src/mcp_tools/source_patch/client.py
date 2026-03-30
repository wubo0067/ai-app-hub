#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# client.py - Source Patch MCP 客户端实现
# Author: CalmWU
# Created: 2026-01-28

# src/mcp_tools/source_patch/client.py
import sys
from typing import List, Optional
from langchain_mcp_adapters.client import MultiServerMCPClient
from src.utils.logging import logger

# 初始化 MCP 客户端
patch_client = MultiServerMCPClient(
    {
        "source_patch": {
            "command": sys.executable,
            "args": ["-m", "src.mcp_tools.source_patch.server"],
            "transport": "stdio",
        }
    }
)

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
        logger.debug(
            "\n".join(f"{tool.name}: {tool.description}" for tool in _patch_tools)
        )
        logger.info(f"Successfully initialized {len(_patch_tools)} source_patch tools.")
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


# 模块级别的工具列表（可能为空，需要在运行时初始化）
patch_tools = []
