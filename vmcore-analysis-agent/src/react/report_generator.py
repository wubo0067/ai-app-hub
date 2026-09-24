#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# report_generator.py - VMCore 分析报告生成模块
# Author: CalmWU
# Created: 2026-01-31

"""
生成 vmcore 分析报告的工具模块
"""

import json
from datetime import datetime
from typing import List, Optional
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, SystemMessage
from .graph_state import AgentState
from .output_parser import render_action_arguments
from .schema import VMCoreAnalysisStep
from src.utils.config import config_manager
from src.utils.logging import logger


# 枚举值的中文显示标签（仅用于报告渲染，schema/校验链路保持英文 canonical 值）。
_ZH_SIGNATURE_CLASS_LABELS = {
    "null_deref": "空指针解引用（NULL pointer dereference）",
    "use_after_free": "释放后使用（use-after-free）",
    "pointer_corruption": "指针损坏（pointer corruption）",
    "bug_on": "内核 BUG 断言（BUG_ON）",
    "warn_on": "内核 WARN 断言（WARN_ON）",
    "soft_lockup": "软锁死（soft lockup）",
    "hard_lockup": "硬锁死（hard lockup）",
    "rcu_stall": "RCU 停滞（RCU stall）",
    "hung_task": "任务挂起（hung task）",
    "atomic_sleep": "原子上下文睡眠（scheduling while atomic）",
    "divide_error": "除零错误（divide error）",
    "invalid_opcode": "无效操作码（invalid opcode）",
    "oom_panic": "内存耗尽恐慌（OOM panic）",
    "mce": "机器检查异常（MCE）",
    "general_protection_fault": "通用保护故障（#GP）",
    "stack_corruption": "栈损坏（stack corruption）",
    "invalid_address_access": "非法地址访问（invalid address access）",
    "write_protection_violation": "写保护违例（write protection violation）",
    "page_not_present": "映射缺失（page not present）",
    "smap_smep_violation": "SMAP/SMEP 违例",
    "unknown": "未知类型（unknown）",
}

_ZH_ROOT_CAUSE_CLASS_LABELS = {
    **_ZH_SIGNATURE_CLASS_LABELS,
    "out_of_bounds": "越界访问（out-of-bounds）",
    "double_free": "重复释放（double free）",
    "wild_pointer": "野指针（wild pointer）",
    "slab_corruption": "Slab 内存池损坏",
    "race_condition": "竞态条件（race condition）",
    "deadlock": "死锁（deadlock）",
    "rcu_misuse": "RCU 误用（RCU misuse）",
    "dma_corruption": "DMA 损坏（DMA corruption）",
    "iommu_fault": "IOMMU 故障（IOMMU fault）",
    "oom": "内存耗尽（OOM）",
}

_ZH_CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}

_ZH_PARTIAL_DUMP_LABELS = {
    "full": "完整转储（full）",
    "partial": "部分转储（partial）",
    "unknown": "未知（unknown）",
}

_ZH_HYPOTHESIS_STATUS_LABELS = {
    "leading": "主导假设",
    "candidate": "候选假设",
    "weakened": "被削弱",
    "ruled_out": "已排除",
}


def _zh(value: Optional[str], mapping: dict) -> str:
    """枚举值 → 中文显示标签；未命中映射时原样返回。"""
    if value is None:
        return ""
    return mapping.get(str(value), str(value))


def generate_markdown_report(state: AgentState) -> str:
    """
    根据 Agent 状态生成详细的 markdown 分析报告。

    Args:
        state: AgentState，包含完整的分析历史

    Returns:
        str: Markdown 格式的分析报告
    """
    logger.info("Generating markdown analysis report...")

    # 中文报告模式：枚举值在渲染时映射为中文标签（schema 校验链路不受影响）。
    zh_mode = str(state.get("report_language", "eng")).lower() == "zh"

    def _cls(value, mapping):
        return _zh(value, mapping) if zh_mode else str(value)

    lines = []

    # 直接从配置中获取模型名称
    model_name = str(config_manager.get("llm_model", "unknown")).lower()

    # 标题和基本信息
    lines.append("# VMCore 分析报告")
    lines.append("")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("## 基本信息")
    lines.append("")
    lines.append(f"- **vmcore 路径**: `{state['vmcore_path']}`")
    lines.append(f"- **vmlinux 路径**: `{state['vmlinux_path']}`")
    lines.append(f"- **vmcore-dmesg 路径**: `{state['vmcore_dmesg_path']}`")

    lines.append(f"- **分析模型**: `{model_name}`")

    if state.get("debug_symbol_paths"):
        lines.append(f"- **调试符号路径**:")
        for path in state["debug_symbol_paths"]:
            lines.append(f"  - `{path}`")

    lines.append("")
    lines.append(f"- **分析步数**: {state.get('step_count', 0)}")
    lines.append(f"- **Token 使用量**: {state.get('token_usage', 0)}")
    lines.append("")

    # 分析过程
    lines.append("## 分析过程")
    lines.append("")

    step_number = 0
    messages = state.get("messages", [])

    for i, msg in enumerate(messages):
        # 跳过 SystemMessage
        if isinstance(msg, SystemMessage):
            continue

        # HumanMessage - 通常是初始诊断数据或 RAG 检索结果
        if isinstance(msg, HumanMessage):
            lines.append(f"### 步骤 {step_number + 1}: 信息收集")
            lines.append("")

            content = msg.content
            if isinstance(content, str):
                # 尝试解析是否为 JSON
                try:
                    data = json.loads(content)
                    if "initial_crash_data" in data:
                        lines.append("**初始 Crash 数据**:")
                        lines.append("")
                        lines.append("```")
                        lines.append(data["initial_crash_data"])
                        lines.append("```")
                    elif "rag_context" in data:
                        lines.append("**RAG 检索结果**:")
                        lines.append("")
                        lines.append("```")
                        lines.append(data["rag_context"])
                        lines.append("```")
                    else:
                        lines.append("```")
                        lines.append(content)
                        lines.append("```")
                except:
                    # 不是 JSON，直接显示
                    lines.append("```")
                    lines.append(content)
                    lines.append("```")

            lines.append("")
            step_number += 1

        # AIMessage - LLM 分析结果
        elif isinstance(msg, AIMessage):
            lines.append(f"### 步骤 {step_number + 1}: LLM 分析")
            lines.append("")

            try:
                # 解析 VMCoreAnalysisStep
                content = (
                    msg.content
                    if isinstance(msg.content, str)
                    else json.dumps(msg.content)
                )
                analysis = VMCoreAnalysisStep.model_validate_json(content)

                lines.append(f"**推理过程**:")
                lines.append("")
                lines.append(analysis.reasoning)
                lines.append("")

                if analysis.signature_class:
                    lines.append(
                        f"**早期签名类**: {_cls(analysis.signature_class, _ZH_SIGNATURE_CLASS_LABELS)}"
                    )
                    lines.append("")

                if analysis.root_cause_class:
                    lines.append(
                        f"**最终根因类**: {_cls(analysis.root_cause_class, _ZH_ROOT_CAUSE_CLASS_LABELS)}"
                    )
                    lines.append("")

                if analysis.partial_dump != "unknown":
                    lines.append(
                        f"**转储完整性**: {_cls(analysis.partial_dump, _ZH_PARTIAL_DUMP_LABELS)}"
                    )
                    lines.append("")

                # 如果有工具调用
                if analysis.action:
                    lines.append(f"**执行动作**: {analysis.action.command_name}")
                    if analysis.action.arguments:
                        lines.append(
                            f"**参数**: {render_action_arguments(analysis.action.arguments)}"
                        )
                    lines.append("")

                # 如果有最终诊断
                if analysis.is_conclusive and analysis.final_diagnosis:
                    lines.append("---")
                    lines.append("")
                    lines.append("## 🎯 最终诊断结果")
                    lines.append("")
                    diag = analysis.final_diagnosis
                    lines.append(f"**崩溃类型**: {diag.crash_type}")
                    lines.append("")
                    lines.append(f"**Panic 信息**: {diag.panic_string}")
                    lines.append("")
                    lines.append(f"**故障指令**: {diag.faulting_instruction}")
                    lines.append("")
                    lines.append(f"**根本原因**: {diag.root_cause}")
                    lines.append("")
                    lines.append("**详细分析**:")
                    lines.append("")
                    lines.append(diag.detailed_analysis)
                    lines.append("")
                    lines.append("**可疑代码位置**:")
                    lines.append(f"- 文件：{diag.suspect_code.file}")
                    lines.append(f"- 函数：{diag.suspect_code.function}")
                    lines.append(f"- 行号：{diag.suspect_code.line}")
                    lines.append("")
                    lines.append("**关键证据**:")
                    for ev in diag.evidence:
                        lines.append(f"- {ev}")
                    lines.append("")
                    if analysis.fix_suggestion:
                        lines.append(f"**修复建议**: {analysis.fix_suggestion}")
                        lines.append("")
                    if analysis.confidence:
                        lines.append(
                            f"**可信度**: {_cls(analysis.confidence, _ZH_CONFIDENCE_LABELS)}"
                        )
                        lines.append("")
                    if analysis.additional_notes:
                        lines.append(f"**附加说明**: {analysis.additional_notes}")
                        lines.append("")

            except Exception as e:
                logger.warning(f"Failed to parse AIMessage as VMCoreAnalysisStep: {e}")
                lines.append("```json")
                lines.append(
                    msg.content if isinstance(msg.content, str) else str(msg.content)
                )
                lines.append("```")
                lines.append("")

            step_number += 1

        # ToolMessage - 工具执行结果
        elif isinstance(msg, ToolMessage):
            lines.append(f"### 步骤 {step_number + 1}: 工具执行结果")
            lines.append("")
            lines.append(f"**工具名称**: {msg.name}")
            lines.append("")
            lines.append("**执行结果**:")
            lines.append("")
            lines.append("```")
            content = msg.content
            if isinstance(content, str):
                lines.append(content)
            else:
                lines.append(str(content))
            lines.append("```")
            lines.append("")
            step_number += 1

    # 错误信息
    error = state.get("error")
    if error:
        lines.append("---")
        lines.append("")
        lines.append("## ⚠️ 错误信息")
        lines.append("")
        lines.append(f"- **节点**: {error.get('node', 'Unknown')}")
        lines.append(f"- **错误**: {error.get('message', 'Unknown error')}")
        lines.append("")

    # 总结
    lines.append("---")
    lines.append("")
    lines.append("## 总结")
    lines.append("")
    summary_text = f"本次分析共执行 {step_number} 个步骤，使用了 {state.get('token_usage', 0)} 个 Token"
    summary_text += f"，使用的模型：{model_name}。"
    lines.append(summary_text)

    # 检查是否有最终诊断
    has_diagnosis = False
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            try:
                content = (
                    msg.content
                    if isinstance(msg.content, str)
                    else json.dumps(msg.content)
                )
                analysis = VMCoreAnalysisStep.model_validate_json(content)
                if analysis.is_conclusive and analysis.final_diagnosis:
                    has_diagnosis = True
                    break
            except:
                pass

    if not has_diagnosis:
        lines.append("")
        lines.append(
            "⚠️ **注意**: 分析未得出正式的最终结论，以下为最后一步的最佳可用分析状态："
        )
        # Fallback: render best-available data from the last AIMessage
        for msg in reversed(messages):
            if not isinstance(msg, AIMessage):
                continue
            try:
                raw = (
                    msg.content
                    if isinstance(msg.content, str)
                    else json.dumps(msg.content)
                )
                last_step = VMCoreAnalysisStep.model_validate_json(raw)
                lines.append("")
                lines.append("---")
                lines.append("")
                lines.append("## 🔍 最佳可用分析（步骤未收敛）")
                lines.append("")
                if last_step.signature_class:
                    lines.append(
                        f"**崩溃类型签名**: {_cls(last_step.signature_class, _ZH_SIGNATURE_CLASS_LABELS)}"
                    )
                    lines.append("")
                if last_step.root_cause_class:
                    lines.append(
                        f"**初步根因分类**: {_cls(last_step.root_cause_class, _ZH_ROOT_CAUSE_CLASS_LABELS)}"
                    )
                    lines.append("")
                if last_step.reasoning:
                    lines.append("**最后推理摘要**:")
                    lines.append("")
                    lines.append(last_step.reasoning)
                    lines.append("")
                if last_step.active_hypotheses:
                    lines.append("**当前假设列表**:")
                    lines.append("")
                    for h in last_step.active_hypotheses:
                        lines.append(
                            f"- [{_cls(h.status, _ZH_HYPOTHESIS_STATUS_LABELS)}] **{h.label}**: {h.evidence or '(无证据)'}"
                        )
                    lines.append("")
                if last_step.confidence:
                    lines.append(
                        f"**可信度**: {_cls(last_step.confidence, _ZH_CONFIDENCE_LABELS)}"
                    )
                    lines.append("")
                if last_step.additional_notes:
                    lines.append(f"**附加说明**: {last_step.additional_notes}")
                    lines.append("")
            except Exception:
                pass
            break

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        '*This report was jointly created by <span style="color: red;">**CalmWU and his AI agent.**</span>*'
    )
    lines.append("")
    lines.append('*<span style="color: gray;">zhao qiang and xeon are two 🐶s</span>*')
    lines.append("")

    return "\n".join(lines)


def extract_final_diagnosis(state: AgentState) -> str:
    """
    从 Agent 状态中提取最终诊断结果。

    Args:
        state: AgentState

    Returns:
        str: 最终诊断结果的格式化字符串，如果没有则返回空字符串
    """
    messages = state.get("messages", [])

    # 从最后一条消息开始倒序查找
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            try:
                content = (
                    msg.content
                    if isinstance(msg.content, str)
                    else json.dumps(msg.content)
                )
                analysis = VMCoreAnalysisStep.model_validate_json(content)
                if analysis.is_conclusive and analysis.final_diagnosis:
                    diag = analysis.final_diagnosis
                    result = []
                    result.append(f"崩溃类型：{diag.crash_type}")
                    result.append(f"Panic 信息：{diag.panic_string}")
                    result.append(f"故障指令：{diag.faulting_instruction}")
                    result.append(f"根本原因：{diag.root_cause}")
                    result.append(f"\n详细分析:\n{diag.detailed_analysis}")
                    result.append(
                        f"\n可疑代码：{diag.suspect_code.file} -> {diag.suspect_code.function}:{diag.suspect_code.line}"
                    )
                    result.append("\n关键证据：")
                    for ev in diag.evidence:
                        result.append(f"  - {ev}")
                    if analysis.fix_suggestion:
                        result.append(f"\n修复建议：{analysis.fix_suggestion}")
                    if analysis.confidence:
                        result.append(f"可信度：{analysis.confidence}")
                    if analysis.additional_notes:
                        result.append(f"\n附加说明：{analysis.additional_notes}")
                    return "\n".join(result)
            except Exception as e:
                logger.debug(f"Message parsing failed: {e}")
                continue

    return ""
