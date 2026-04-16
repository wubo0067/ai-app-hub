#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""MCP 工具自动发现与注册入口。"""

from .registry import (
    get_registered_tool_provider,
    initialize_all_mcp_tools,
    list_registered_tool_providers,
)

__all__ = [
    "get_registered_tool_provider",
    "initialize_all_mcp_tools",
    "list_registered_tool_providers",
]
