#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.utils.logging import logger

# PayloadBuilder: 针对某个工具名，把 LLM 传来的原始参数和运行时 state
# 组装成最终传给 MCP 工具的 payload。
# 例如 crash 工具会在这里自动补上 vmcore_path / vmlinux_path。
PayloadBuilder = Callable[[str, Any, dict[str, Any]], dict[str, Any]]

# Initializer: 每个 MCP 工具包都需要暴露一个异步初始化函数，
# 负责启动对应的 MCP client，并从 server 侧拉取工具列表。
Initializer = Callable[[], Awaitable[List]]


# frozen=True 是 dataclass 装饰器的一个参数，它的含义是使被装饰的数据类成为不可变的（immutable）。
# 这意味着一旦创建了该数据类的实例，就不能再修改其实例属性。
# 当设置了 frozen=True 时，数据类的所有实例都会变成不可变对象。
# 不可变意味着一旦对象被创建，你就不能修改它的任何字段。
# 尝试修改字段会导致 AttributeError 异常。
@dataclass(frozen=True)
class MCPToolProvider:
    """描述一个 MCP 工具包暴露出来的最小标准接口。

    registry 不关心具体工具是 crash、patch 还是 stack_canary，
    只要求每个子包最终能提供以下 5 个元素：
    1. package_name: 目录名，用来做日志和 provider 身份标识。
    2. server_name: MultiServerMCPClient 中配置的 server key。
    3. client: 已构造好的 MCP client 实例。
    4. initialize_tools: 异步初始化函数，用来获取工具列表。
    5. build_tool_payload: 把原始参数转换成真实调用 payload 的函数。
    """

    # 工具包目录名，例如 "crash"、"source_patch"、"stack_canary"。
    package_name: str
    # provider 在 MCP client 配置中的 server 名称。
    server_name: str
    # 已构造好的 MultiServerMCPClient 实例。
    client: Any
    # 用于异步加载该 provider 全部工具的函数。
    initialize_tools: Initializer
    # 用于按工具名生成请求 payload 的适配函数。
    build_tool_payload: PayloadBuilder


# _providers: provider 级缓存。
# 第一次扫描 src/mcp_tools 子目录后，会把发现结果缓存在这里，
# 后续调用 list_registered_tool_providers() 时直接复用，避免重复 import 子模块。
_providers: Optional[dict[str, MCPToolProvider]] = None

# _tool_name_to_provider: 工具名到 provider 的反向索引。
# initialize_all_mcp_tools() 在真正拿到工具实例后，会建立这个映射，
# 供执行节点根据 tool.name 反查应该路由到哪个 provider。
_tool_name_to_provider: dict[str, MCPToolProvider] = {}


def list_registered_tool_providers() -> dict[str, MCPToolProvider]:
    """扫描 src/mcp_tools 下的子目录，发现所有符合约定的 MCP provider。

    发现规则非常简单：
    1. 必须是一个子目录；
    2. 子目录下必须存在 client.py；
    3. client.py 必须暴露标准接口：MCP_CLIENT、MCP_SERVER_NAME、
       initialize_tools、build_tool_payload。

    返回值会被缓存到 _providers，避免重复扫描和重复 import。
    """

    # 这里需要写 global，因为后面会给模块级缓存 _providers 重新赋值。
    global _providers

    # 如果之前已经扫描过，直接返回缓存，避免重复 import 各 provider。
    if _providers is not None:
        return _providers

    # registry.py 所在目录就是 src/mcp_tools；以它为根遍历所有子目录。
    base_dir = Path(__file__).resolve().parent

    # discovered 用来暂存本次扫描到的 provider。
    discovered: dict[str, MCPToolProvider] = {}

    # 对目录名排序后遍历，这样扫描顺序稳定，日志也更容易对比。
    for child in sorted(base_dir.iterdir()):
        # 只处理普通子目录；像 __pycache__ 这类目录直接跳过。
        if not child.is_dir() or child.name.startswith("__"):
            continue

        # 我们把 client.py 视为“这个子目录想注册成 MCP provider”的标志文件。
        client_file = child / "client.py"
        if not client_file.exists():
            continue

        # 按统一命名约定拼出模块路径，例如 src.mcp_tools.crash.client。
        module_name = f"src.mcp_tools.{child.name}.client"
        try:
            # 动态导入 provider 的 client 模块。
            # 这样新增一个子目录后，无需手工改 registry 的硬编码列表。
            module = importlib.import_module(module_name)
        except Exception as exc:
            # import 失败不能让整个注册流程中断，只记录日志并跳过当前 provider。
            logger.error(
                "Failed to import MCP tool client module %s: %s", module_name, exc
            )
            continue

        # 下面 4 个对象是 registry 约定的“标准 provider API”。
        # 只要子模块暴露了它们，registry 就能把它接进统一执行框架。
        mcp_client = getattr(module, "MCP_CLIENT", None)
        server_name = getattr(module, "MCP_SERVER_NAME", None)
        initialize_tools = getattr(module, "initialize_tools", None)
        build_tool_payload = getattr(module, "build_tool_payload", None)

        # 任意一个标准接口缺失，都说明这个子包还不满足自动注册约定。
        if (
            mcp_client is None
            or not server_name
            or not callable(initialize_tools)
            or not callable(build_tool_payload)
        ):
            logger.warning(
                "Skipping MCP tool package %s because it does not expose the standard client API.",
                child.name,
            )
            continue

        # 把发现到的 provider 封装成统一结构，供后续初始化与路由使用。
        discovered[child.name] = MCPToolProvider(
            package_name=child.name,
            server_name=server_name,
            client=mcp_client,
            initialize_tools=initialize_tools,
            build_tool_payload=build_tool_payload,
        )

    # 扫描结束后写入缓存，后续直接复用。
    _providers = discovered
    return discovered


async def initialize_all_mcp_tools() -> List:
    """初始化所有已发现的 MCP provider，并建立 tool -> provider 反向映射。"""

    # 第一步：拿到所有已注册的 provider。
    # 这些 provider 只是“被发现”，不代表工具已经真的加载完成。
    providers = list_registered_tool_providers()

    # 输出 provider 名称，便于启动阶段确认自动发现结果。
    logger.info(f"Discovered MCP tool providers: {list(providers.keys())}")

    # 汇总所有 provider 返回的工具实例。
    all_tools: List = []

    # 每次初始化前都清空旧的反向索引，避免陈旧映射残留。
    _tool_name_to_provider.clear()

    # 依次初始化每个 provider。
    for provider in providers.values():
        try:
            # 这里真正启动 client 并从对应 server 拉取工具定义。
            tools = await provider.initialize_tools()
        except Exception as exc:
            # 单个 provider 初始化失败时，不影响其他 provider 继续加载。
            logger.error(
                "Failed to initialize MCP tools from provider %s: %s",
                provider.package_name,
                exc,
            )
            continue

        # 把当前 provider 的工具并入总列表，供主程序统一 bind 到 LLM。
        all_tools.extend(tools)

        # 同时建立 tool.name -> provider 的反向索引。
        # 这个索引是运行时执行工具调用时做路由的关键数据结构。
        for tool in tools:
            # 如果出现重名工具，先看之前是不是已经有别的 provider 注册过。
            existing = _tool_name_to_provider.get(tool.name)

            # 同名且来自不同 provider 时，给出警告。
            # 当前策略是“后写覆盖前写”，因此最后一个会生效。
            if existing is not None and existing.package_name != provider.package_name:
                logger.warning(
                    "Duplicate MCP tool name detected: %s from %s overrides %s",
                    tool.name,
                    provider.package_name,
                    existing.package_name,
                )

            # 记录最终生效的 tool -> provider 映射。
            _tool_name_to_provider[tool.name] = provider

    # 统一记录加载结果，便于在启动日志里快速确认工具规模。
    logger.info(
        "Initialized %d MCP providers and %d MCP tools.",
        len(providers),
        len(all_tools),
    )

    # 返回扁平化后的工具列表，供 main.py / graph.py 统一绑定到 LLM。
    return all_tools


def get_registered_tool_provider(tool_name: str) -> Optional[MCPToolProvider]:
    """根据工具名反查 provider。

    正常情况下，这个映射会在 initialize_all_mcp_tools() 中建立。
    这里保留一个轻量兜底：如果映射为空，至少先触发 provider 发现。
    注意，这个兜底不会自动初始化工具，因此如果 tool_name 尚未建立映射，
    返回值仍可能是 None。
    """

    # 如果反向索引还没有建立，至少先确保 provider 目录已经被扫描过。
    if not _tool_name_to_provider:
        list_registered_tool_providers()

    # 返回与该工具名对应的 provider；若不存在则返回 None。
    return _tool_name_to_provider.get(tool_name)
