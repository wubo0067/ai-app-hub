"""
生成 vmcore 分析报告的工具模块
"""

import json
from datetime import datetime
from typing import List
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, SystemMessage
from .graph_state import AgentState
from .llm_node import VMCoreAnalysisStep
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
                        lines.append(content[:500])  # 限制长度
                        if len(content) > 500:
                            lines.append("...")
                        lines.append("```")
                except:
                    # 不是 JSON，直接显示
                    lines.append("```")
                    lines.append(content[:500])
                    if len(content) > 500:
                        lines.append("...")
                    lines.append("```")

            lines.append("")
            step_number += 1

        # AIMessage - LLM 分析结果
        elif isinstance(msg, AIMessage):
            lines.append(f"### 步骤 {step_number + 1}: LLM 分析")
            lines.append("")

            try:
                # 解析 VMCoreAnalysisStep
                content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
                analysis = VMCoreAnalysisStep.model_validate_json(content)

                lines.append(f"**推理过程**:")
                lines.append("")
                lines.append(analysis.reasoning)
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
                    lines.append(analysis.final_diagnosis)
                    lines.append("")

            except Exception as e:
                logger.warning(f"Failed to parse AIMessage as VMCoreAnalysisStep: {e}")
                lines.append("```json")
                lines.append(msg.content[:500])
                if len(msg.content) > 500:
                    lines.append("...")
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
                lines.append(content[:1000])  # 限制长度
                if len(content) > 1000:
                    lines.append("...")
            else:
                lines.append(str(content)[:1000])
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
        f"本次分析共执行 {step_number} 个步骤，使用了 {state.get('token_usage', 0)} 个 Token。"
    )

    # 检查是否有最终诊断
    has_diagnosis = False
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            try:
                content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
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

    return "\n".join(lines)


def extract_final_diagnosis(state: AgentState) -> str:
    """
    从 Agent 状态中提取最终诊断结果。

    Args:
        state: AgentState

    Returns:
        str: 最终诊断结果，如果没有则返回空字符串
    """
    messages = state.get("messages", [])

    # 从最后一条消息开始倒序查找
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            try:
                content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
                analysis = VMCoreAnalysisStep.model_validate_json(content)
                if analysis.is_conclusive and analysis.final_diagnosis:
                    return analysis.final_diagnosis
            except Exception as e:
                logger.debug(f"Message parsing failed: {e}")
                continue

    return ""
