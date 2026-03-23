#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# client.py - Crash MCP 客户端实现
# Author: CalmWU
# Created: 2026-01-09

# src/mcp_tools/crash/client.py
import sys
from typing import List, Optional
from langchain_mcp_adapters.client import MultiServerMCPClient
from src.utils.logging import logger

# 初始化 MCP 客户端
crash_client = MultiServerMCPClient(
    {
        "crash": {
            "command": sys.executable,
            "args": ["-m", "src.mcp_tools.crash.server"],
            "transport": "stdio",
        }
    }
)

# 全局工具列表（延迟初始化）
_crash_tools: Optional[List] = None


async def _initialize_tools():
    """异步初始化工具列表"""
    global _crash_tools
    if _crash_tools is not None:
        return _crash_tools

    try:
        logger.info("Initializing crash MCP tools...")
        _crash_tools = await crash_client.get_tools()
        # 详细打印 tool 信息
        logger.debug(
            "\n".join(f"{tool.name}: {tool.description}" for tool in _crash_tools)
        )
        logger.info(f"Successfully initialized {len(_crash_tools)} crash tools.")
        return _crash_tools
    except Exception as e:
        logger.error(f"Failed to get tools from MCP client: {e}")
        _crash_tools = []
        return []


def get_crash_tools() -> List:
    """
    获取 crash 工具列表（同步接口）

    注意：这个函数会尝试在当前事件循环中获取工具，
    如果没有运行中的事件循环，将返回空列表并记录警告。
    """
    global _crash_tools

    # 如果已经初始化过，直接返回
    if _crash_tools is not None:
        return _crash_tools

    try:
        import asyncio

        # 尝试获取当前运行的事件循环
        loop = asyncio.get_running_loop()
        logger.warning(
            "Cannot initialize tools synchronously in a running event loop. "
            "Please call 'await initialize_crash_tools()' explicitly in async context."
        )
        return []
    except RuntimeError:
        # 没有运行中的事件循环，可以安全地使用 asyncio.run
        import asyncio

        try:
            return asyncio.run(_initialize_tools())
        except Exception as e:
            logger.error(f"Failed to run async tool initialization: {e}")
            return []


async def initialize_crash_tools() -> List:
    """
    异步初始化 crash 工具（推荐在 async 上下文中使用）

    Returns:
        List: crash 工具列表
    """
    return await _initialize_tools()


# 模块级别的工具列表（可能为空，需要在运行时初始化）
# 推荐使用 initialize_crash_tools() 在 async 上下文中初始化
crash_tools = []

logger.info(
    "Crash MCP client initialized. Call 'await initialize_crash_tools()' to load tools."
)
