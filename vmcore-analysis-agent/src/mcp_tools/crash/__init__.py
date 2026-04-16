#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Crash MCP 工具包。

注意：不要在包级别导入 client。
server 通过 `python -m src.mcp_tools.crash.server` 启动时，Python 会先执行本包
的 __init__；如果这里导入 client，会在 server 进程里再次初始化 MCP client，
破坏 stdio 握手。
"""
