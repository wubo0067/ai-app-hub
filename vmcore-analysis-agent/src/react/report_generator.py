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
from typing import List
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, SystemMessage
from .graph_state import AgentState
from .schema import VMCoreAnalysisStep
from src.utils.logging import logger


def generate_markdown_report(state: AgentState) -> str:
    """
    根据 Agent 状态生成详细的 markdown 分析报告。

    Args:
        state: AgentState，包含完整的分析历史

    Returns:
        str: Markdown 格式的分析报告
    """
    logger.info("Generating markdown analysis report...")

    lines = []

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
                    lines.append(f"**早期签名类**: {analysis.signature_class}")
                    lines.append("")

                if analysis.root_cause_class:
                    lines.append(f"**最终根因类**: {analysis.root_cause_class}")
                    lines.append("")

                if analysis.partial_dump != "unknown":
                    lines.append(f"**转储完整性**: {analysis.partial_dump}")
                    lines.append("")

                # 如果有工具调用
                if analysis.action:
                    lines.append(f"**执行动作**: {analysis.action.command_name}")
                    if analysis.action.arguments:
                        lines.append(f"**参数**: {' '.join(analysis.action.arguments)}")
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
                        lines.append(f"**可信度**: {analysis.confidence}")
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
    lines.append(
        f"本次分析共执行 {step_number} 个步骤，使用了 {state.get('token_usage', 0)} 个 Token"
    )
    if state.get("model_name"):
        lines.append(f"，使用的模型：{state.get('model_name')}")
    lines.append("。")

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
            "⚠️ **注意**: 分析未得出最终结论，可能需要更多信息或达到了步数限制。"
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        '*This report was jointly created by <span style="color: red;">**CalmWU and his AI agent.**</span>*'
    )
    lines.append("")
    lines.append("*🐶 xeon*")  # Navigator Oracle Explorer X-ray
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
