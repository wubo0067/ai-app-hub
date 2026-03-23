#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# __init__.py - Crash MCP 工具客户端模块包初始化
# Author: CalmWU
# Created: 2026-01-09

# src/mcp_tools/crash/__init__.py
"""
Crash MCP 工具客户端模块

提供与 crash 分析工具的 MCP 协议交互能力。
"""

from .client import crash_client, crash_tools

__all__ = ["crash_client", "crash_tools"]
