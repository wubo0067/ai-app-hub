#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from typing import Iterable, Optional, Sequence, cast

from langchain_core.messages import AIMessage, BaseMessage

from .graph_state import AgentState
from .prompt_layers import LAYER0_SYSTEM_PROMPT_TEMPLATE, PLAYBOOKS, SOP_FRAGMENTS
from .prompts import build_minimal_schema_enum_contract
from .schema import CrashSignatureClass, GateEntry, Hypothesis, VMCoreLLMAnalysisStep


def build_analysis_system_prompt(state: AgentState, *, is_last_step: bool) -> str:
    """
    构建 VMCore 分析 Agent 的系统提示词（System Prompt）。

    该函数负责组装完整的系统提示词，为 LLM 提供分析 Linux 内核崩溃 (vmcore) 所需的上下文信息、
    分析规则和输出约束。提示词采用分层架构设计，包含基础系统模板、当前执行状态、
    针对性分析剧本和标准操作程序片段。

    Args:
        state (AgentState): 包含当前分析状态的字典对象，必须包含以下关键字段：
            - current_signature_class: 当前崩溃签名类别，用于选择对应的分析剧本
            - managed_active_hypotheses: 当前活跃的假设列表
            - managed_gates: 当前门控验证状态
            - messages: 历史消息记录
            - step_count: 当前分析步骤计数
            - 其他相关状态字段

        is_last_step (bool): 标识是否为最后一步分析。当为 True 时，会添加关键警告信息，
            强制 LLM 在此步骤提供最终诊断结论，不得再请求工具调用。

    Returns:
        str: 完整的系统提示词字符串，包含以下组成部分（按顺序）：
            1. 基础系统提示模板（包含 VMCoreAnalysisStep 的 JSON Schema）
            2. 当前执行器状态摘要（通过 build_executor_state_section 生成）
            3. 针对当前崩溃签名的专用分析剧本（如果存在）
            4. 相关的标准操作程序 (SOP) 片段（如果适用）
            5. 结构化输出约束说明
            6. 最后步骤的关键警告（仅当 is_last_step=True 时）

    使用场景：
        - 在每次 LLM 分析步骤开始前调用，为 DeepSeek-Reasoner 模型提供完整的分析上下文
        - 支持 ReAct 模式的"思考 - 行动 - 观察"循环，确保 LLM 遵循预定义的分析流程
        - 动态调整提示词内容，根据当前崩溃类型和分析阶段提供针对性指导

    注意事项：
        - 所有非空的提示词部分都会被连接，空字符串会被自动过滤
        - 剧本 (playbook) 的选择严格基于 current_signature_class，确保分析路径的正确性
        - SOP 片段的选择基于多种条件（签名类别、根本原因、步骤计数、门控状态、近期命令等）
        - 必须明确告知 LLM 哪些字段由执行器管理，避免 LLM 尝试维护这些状态导致幻觉
    """
    prompt_parts = [
        LAYER0_SYSTEM_PROMPT_TEMPLATE.format(
            VMCoreAnalysisStep_Schema=json.dumps(
                VMCoreLLMAnalysisStep.model_json_schema(),
                indent=2,
            ),
        ),
        "[ENUM CONTRACT]\n" + build_minimal_schema_enum_contract(),
        build_executor_state_section(state),
    ]

    playbook = _select_playbook(state)
    if playbook:
        prompt_parts.append(playbook)

    sop_fragments = _select_sop_fragments(state)
    if sop_fragments:
        prompt_parts.extend(sop_fragments)

    prompt_parts.append(
        "[STRUCTURED OUTPUT CONTRACT]\n"
        "active_hypotheses and gates are executor-managed. "
        "Omit them entirely from your JSON and return only the minimal schema fields."
    )

    if is_last_step:
        prompt_parts.append(
            "[CRITICAL WARNING]\n"
            "This is your LAST STEP. You have reached the execution limit.\n"
            "You MUST provide a final_diagnosis based on the information gathered so far.\n"
            "Set is_conclusive to true and do NOT request any further tool calls (action must be null)."
        )

    return "\n\n".join(part for part in prompt_parts if part)


def build_executor_state_section(state: AgentState) -> str:
    """
    构建执行器当前状态信息的格式化字符串。

    该函数从 AgentState 中提取关键状态信息，包括签名类别、根本原因类别、部分转储信息、
    步骤计数等，并结合假设、门控状态和最近执行的命令，生成一个结构化的状态摘要。
    这个摘要用于在系统提示中向 LLM 提供当前调查的上下文信息。

    Args:
        state (AgentState): 包含当前代理状态信息的字典对象，包含各种调查相关的状态字段

    Returns:
        str: 格式化的状态信息字符串，包含步骤编号、签名类别、根本原因类别、部分转储、
             当前阶段、活跃假设、门控状态和已执行命令等信息，每行以 Markdown 列表项格式呈现
    """
    signature_class = state.get("current_signature_class") or "unknown"
    root_cause_class = state.get("current_root_cause_class") or "unknown"
    partial_dump = state.get("current_partial_dump") or "unknown"
    step_count = state.get("step_count", 0)

    hypotheses = _format_hypotheses(state.get("managed_active_hypotheses"))
    gates = _format_gates(state.get("managed_gates"))
    recent_commands = _recent_command_summaries(state.get("messages", []))
    stage_name = _infer_stage_name(step_count, state.get("managed_gates"))

    lines = [
        f"## Current Investigation State (Step {step_count})",
        f"- Signature class: {signature_class}",
        f"- Root cause class: {root_cause_class}",
        f"- Partial dump: {partial_dump}",
        f"- Current stage: {stage_name}",
        f"- Active hypotheses: {hypotheses}",
        f"- Gate status: {gates}",
        f"- Commands already run (do not repeat): {recent_commands}",
    ]
    return "\n".join(lines)


def _select_playbook(state: AgentState) -> str:
    """
    根据崩溃签名类别和上下文选择对应的剧本 (playbook) 内容。

    该函数用于从预定义的 PLAYBOOKS 字典中获取与指定崩溃签名类别相关联的剧本内容。
    如果传入的签名类别为空或为"unknown"，则返回空字符串。

    Args:
        state (AgentState): 当前分析状态。

    Returns:s
        str: 对应的剧本内容字符串，如果找不到匹配项则返回空字符串
    """
    signature_class = state.get("current_signature_class")
    if not signature_class or signature_class == "unknown":
        return ""

    recent_text = _recent_text_blob(state.get("messages", []))
    if _is_stack_protector_case(signature_class, recent_text):
        return PLAYBOOKS.get("stack_protector_canary", "")

    return PLAYBOOKS.get(signature_class, "")


def _select_sop_fragments(state: AgentState) -> list[str]:
    """
    根据当前调查状态选择应注入的 SOP 片段。

    该函数会综合签名类别、根因类别、步骤进度、门控状态以及最近对话文本中的
    关键词，动态拼装用于指导 LLM 的 SOP 提示片段。返回结果会在末尾去重并保持
    首次出现顺序，以避免重复指令干扰模型决策。

    Args:
        state (AgentState): 当前分析状态，包含签名分类、步骤计数、门控信息与消息历史。

    Returns:
        list[str]: 需要追加到系统提示中的 SOP 片段列表（已去重，按触发顺序保留）。
    """
    fragments: list[str] = []
    signature_class = state.get("current_signature_class")
    root_cause_class = state.get("current_root_cause_class")
    step_count = state.get("step_count", 0)
    gates = cast(Optional[dict[str, GateEntry]], state.get("managed_gates")) or {}
    recent_text = _recent_text_blob(state.get("messages", []))
    lowered_recent_text = recent_text.lower()
    stack_protector_case = _is_stack_protector_case(signature_class, recent_text)

    external_gate = gates.get("external_corruption_gate")
    local_gate = gates.get("local_corruption_exclusion")
    dma_gate_open = external_gate is not None and external_gate.status in {
        "open",
        "blocked",
    }
    local_exclusion_ready = local_gate is not None and local_gate.status in {
        "closed",
        "n/a",
    }

    # DMA 相关风险在中后期满足门控/关键词证据时优先提示。
    if (
        signature_class in {"pointer_corruption", "use_after_free"}
        and step_count >= 10
        and (
            root_cause_class == "dma_corruption"
            or (dma_gate_open and local_exclusion_ready)
            or "dma" in lowered_recent_text
            or "iommu" in lowered_recent_text
        )
    ):
        fragments.append(SOP_FRAGMENTS["dma_corruption"])

    if any(
        token in lowered_recent_text for token in ("%gs", "per-cpu", "per_cpu", "gs:")
    ):
        fragments.append(SOP_FRAGMENTS["per_cpu_access"])

    # 出现地址搜索相关操作时，补充地址空间与映射检查 SOP。
    if any(
        token in lowered_recent_text
        for token in ("search", "address", "ptov", "kmem -p")
    ):
        fragments.append(SOP_FRAGMENTS["address_search"])

    # 指针/链表损坏线索出现后，增强驱动源码关联排查指导。
    if (
        signature_class in {"pointer_corruption", "use_after_free"}
        and step_count >= 8
        and any(
            token in lowered_recent_text
            for token in (
                "function pointer",
                "_base_",
                "mod -s",
                "sym ",
                "apic",
                "fee0",
                "list_head",
                "self-referential",
                "self reference",
            )
        )
    ):
        fragments.append(SOP_FRAGMENTS["driver_source_correlation"])

    if (
        "stack overflow" in lowered_recent_text
        or "stack corruption" in lowered_recent_text
    ) and not stack_protector_case:
        fragments.append(SOP_FRAGMENTS["stack_overflow"])

    if stack_protector_case:
        fragments.append(SOP_FRAGMENTS["stack_protector_fast_path"])
    elif signature_class == "stack_corruption":
        fragments.append(SOP_FRAGMENTS["stack_frame_forensics"])

    if "kasan" in lowered_recent_text or "ubsan" in lowered_recent_text:
        fragments.append(SOP_FRAGMENTS["kasan_ubsan"])

    # 在后期收敛阶段，为高风险签名注入高级分析技巧。
    if step_count >= 18 and signature_class in {
        "pointer_corruption",
        "use_after_free",
        "general_protection_fault",
    }:
        fragments.append(SOP_FRAGMENTS["advanced_techniques"])

    return _dedupe_preserve_order(fragments)


def _is_stack_protector_case(
    signature_class: Optional[CrashSignatureClass], recent_text: str
) -> bool:
    """
    判断当前 stack_corruption 是否为显式的 stack-protector / __stack_chk_fail 场景。

    这类 case 需要使用经过上下文裁剪的 canary 专用 playbook/SOP，避免将
    generic stack smearing、residual pollution 等叙事与 canary 规则同时注入。
    """
    if signature_class != "stack_corruption":
        return False

    lowered_recent_text = recent_text.lower()
    return any(
        marker in lowered_recent_text
        for marker in (
            "stack-protector",
            "__stack_chk_fail",
            "kernel stack is corrupted in",
        )
    )


def _format_hypotheses(raw_hypotheses: object) -> str:
    """
    将活跃假设列表格式化为紧凑可读的摘要字符串。

    输入可能为空或包含任意对象；函数会按 Hypothesis 模型进行校验，
    最多展示前三条，输出形如 "#1 label (status); #2 label (status)"。

    Args:
        raw_hypotheses (object): 原始假设列表数据。

    Returns:
        str: 格式化后的假设摘要；无有效数据时返回 "none"。
    """
    hypotheses = cast(Optional[Sequence[Hypothesis]], raw_hypotheses)
    if not hypotheses:
        return "none"

    formatted = []
    # 仅展示前 3 条，避免状态摘要过长影响提示词预算。
    for hypothesis in hypotheses[:3]:
        item = Hypothesis.model_validate(hypothesis)
        # 优先展示 rank；若缺失则回退到稳定 id。
        rank = f"#{item.rank}" if item.rank is not None else item.id
        formatted.append(f"{rank} {item.label} ({item.status})")
    return "; ".join(formatted)


def _format_gates(raw_gates: object) -> str:
    """
    将门控状态字典格式化为简洁的键值对字符串。

    每个门控条目会通过 GateEntry 模型校验，最多展示前五项，输出形如
    "gate_a=open, gate_b=closed"，用于在系统提示中快速呈现当前门控进度。

    Args:
        raw_gates (object): 原始门控状态字典。

    Returns:
        str: 格式化后的门控状态摘要；无数据时返回 "none"。
    """
    gates = cast(Optional[dict[str, GateEntry]], raw_gates)
    if not gates:
        return "none"

    formatted = []
    # 仅展示前 5 个 gate，平衡可读性与信息密度。
    for gate_name, gate_entry in list(gates.items())[:5]:
        gate = GateEntry.model_validate(gate_entry)
        formatted.append(f"{gate_name}={gate.status}")
    return ", ".join(formatted)


def _recent_command_summaries(messages: Sequence[BaseMessage]) -> str:
    commands: list[str] = []
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue

        commands.extend(_extract_commands_from_ai_message(message))
        if len(commands) >= 8:
            break

    if not commands:
        return "none"

    unique_commands = _dedupe_preserve_order(reversed(commands))
    return "; ".join(unique_commands[-8:])


def _extract_commands_from_ai_message(message: AIMessage) -> list[str]:
    commands: list[str] = []

    for tool_call in message.tool_calls or []:
        name = tool_call.get("name") or tool_call.get("command_name")
        args = tool_call.get("args") or tool_call.get("arguments") or {}
        if isinstance(args, dict):
            if "command_name" in args:
                command_name = args["command_name"]
                command_args = args.get("arguments") or []
                commands.append(_render_command(command_name, command_args))
            elif "script" in args:
                commands.append(_render_command(str(name), [args["script"]]))
            elif "command" in args:
                commands.append(_render_command(str(name), [args["command"]]))
            else:
                commands.append(str(name))
        elif name:
            commands.append(_render_command(str(name), args))

    if commands:
        return commands

    try:
        parsed_content = json.loads(message.content)
    except (TypeError, json.JSONDecodeError):
        return []

    action = parsed_content.get("action")
    if not action:
        return []
    return [
        _render_command(
            action.get("command_name", "unknown"), action.get("arguments") or []
        )
    ]


def _render_command(command_name: str, arguments: object) -> str:
    if isinstance(arguments, str):
        rendered_args = arguments
    elif isinstance(arguments, Iterable):
        rendered_args = ", ".join(str(arg) for arg in arguments)
    else:
        rendered_args = ""

    return f"{command_name}({rendered_args})" if rendered_args else command_name


def _recent_text_blob(messages: Sequence[BaseMessage]) -> str:
    parts: list[str] = []
    for message in messages[-6:]:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            parts.append(content[:800])
    return "\n".join(parts)


def _infer_stage_name(
    step_count: int,
    gates: Optional[dict[str, GateEntry]],
) -> str:
    if step_count <= 3:
        return "Stage 0-1: panic classification and fault identification"

    if gates:
        open_gates = [
            name for name, gate in gates.items() if gate.status in {"open", "blocked"}
        ]
        if open_gates:
            return f"Stage 2-5: evidence collection ({open_gates[0]} pending)"

    if step_count >= 20:
        return "Stage 6: convergence and bounded conclusion"

    return "Stage 4-5: object validation and source exclusion"


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
