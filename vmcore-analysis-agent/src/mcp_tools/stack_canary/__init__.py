#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Stack canary MCP 工具包。

不要在包级别导入 client，避免 server 入口在模块解析阶段递归创建 MCP client。
"""
