#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# graph_state.py - VMCore 分析 Agent 图状态定义模块
# Author: CalmWU
# Created: 2026-01-09

from dataclasses import dataclass, field
from typing import Annotated, Optional, TypedDict, Union, cast, Sequence
from pydantic import Field
from langgraph.graph import MessagesState
from langgraph.managed import IsLastStep
from operator import add

from .schema import (
    CrashSignatureClass,
    GateEntry,
    Hypothesis,
    PartialDumpStatus,
    RootCauseClass,
)


class AgentError(TypedDict):
    """
    Agent 执行过程中的错误信息。

    Attributes:
        message: 错误描述信息
        node: 发生错误的节点名称
        is_error: 是否为错误状态标记
    """

    message: str
    node: str
    is_error: bool


class AgentState(MessagesState):
    """
    VMCore 分析 Agent 的状态定义，扩展自 MessagesState。

    包含 vmcore 分析所需的路径、命令配置、分析步数控制及错误状态等信息。
    """

    # vmcore 转储文件路径，是 crash 分析的核心输入文件。
    vmcore_path: str
    # 与 vmcore 对应的 dmesg 文本路径，用于补充内核日志上下文。
    vmcore_dmesg_path: str
    # 未裁剪内核符号文件 vmlinux 路径，供 crash 和符号解析使用。
    vmlinux_path: str
    # 第三方内核调试符号路径列表，用于在缺少主符号时补充符号解析能力。
    debug_symbol_paths: Sequence[str]
    # mpykdump 脚本路径
    mpykdump_path: Optional[str]

    # 当前图执行的累计分析步数。
    # 该字段通过 operator.add 聚合，适合在多个节点返回增量值后自动累加。
    step_count: Annotated[int, add]

    # LLM 调用过程中累计消耗的 token 数量。
    # 同样通过 operator.add 聚合，便于统计一次完整分析流程的成本。
    token_usage: Annotated[int, add]

    # LangGraph 管理的终止标记。
    # 当图执行达到 recursion_limit 或运行时判定为最后一步时，该值为 True。
    is_last_step: IsLastStep
    # DeepSeek-Reasoner 等推理模型生成的纯文本 reasoning 内容。
    # 当原始推理结果无法直接作为结构化输出使用时，会先暂存在这里。
    reasoning_to_structure: Optional[str]
    # 原始 additional_kwargs（通常包含 reasoning_content 等扩展字段）。
    # 用于在结构化改写后，仍保留原始 AIMessage 的附加上下文信息。
    reasoning_additional_kwargs: Optional[dict]
    # Agent 最终输出给用户的分析结论或当前阶段性结论文本。
    agent_answer: str
    # 已执行命令的指纹列表。
    # 该字段为 append-only，读取时通常按 set 语义去重，用于避免重复执行相同命令。
    executed_fingerprints: Annotated[list[str], add]
    # 命令指纹到最近一次工具输出内容的映射缓存。
    # 用于 executor 在遇到相同命令时直接复用结果，减少重复调用外部工具。
    tool_output_cache: dict[str, str]
    # 当前仍处于活跃状态的假设列表。
    # 用于在多轮推理过程中持续追踪尚未证伪、尚需进一步验证的根因假设。
    managed_active_hypotheses: Optional[list[Hypothesis]]
    # 当前分析流程中的 gate 状态表。
    # 每个 gate 表示一个分析关卡、判定点或前置条件，用于控制后续推理路径。
    managed_gates: Optional[dict[str, GateEntry]]
    # 当前识别出的 crash 签名分类结果。
    # 用于标识本次 vmcore 更接近哪类崩溃模式或故障签名。
    current_signature_class: Optional[CrashSignatureClass]
    # 当前推断出的根因分类结果。
    # 该字段表示系统目前对 crash 根本原因所属类别的判断。
    current_root_cause_class: Optional[RootCauseClass]
    # 当前 vmcore 是否属于 partial dump 及其判定状态。
    # 部分转储会影响可见数据范围，因此会直接影响分析策略。
    current_partial_dump: Optional[PartialDumpStatus]
    # crash 路径上涉及的关键结构体偏移列表。
    # 用于记录分析过程中提取到的结构偏移信息，辅助后续结构布局校验。
    crash_path_struct_offsets: Optional[list[int]]
    # 结构体布局缓存。
    # key 通常为结构体名称，value 为该结构体字段布局、偏移等解析结果，避免重复计算。
    struct_layout_cache: dict[str, dict[str, object]]
    # Agent 当前错误状态。
    # 当某个节点执行失败或出现不可恢复问题时，会在此记录结构化错误信息。
    error: Optional[AgentError]
