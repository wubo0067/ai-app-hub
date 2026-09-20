#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# llm_node.py - LLM 分析节点实现模块
# Author: CalmWU
# Created: 2026-01-19

import json
from typing import Any, cast
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage, HumanMessage
from .graph_state import AgentState
from .nodes import llm_analysis_node, structure_reasoning_node
from .output_parser import (
    apply_executor_consistency_audit,
    build_tool_calls,
    repair_analysis_step,
    repair_structured_output,
    select_analysis_content,
)
from .llm_runtime import ainvoke_with_retry, compress_messages_for_llm
from .llm_runtime import compute_adaptive_max_tokens
from .prompt_builder import build_analysis_system_prompt, build_executor_state_section
from .prompts import (
    build_structure_reasoning_force_conclusion,
    crash_init_data_prompt,
    simplified_structure_reasoning_prompt,
)
from .schema import VMCoreAnalysisStep, VMCoreLLMAnalysisStep
from .state_manager import project_managed_analysis_step
from src.utils.logging import logger


def _reasoning_signals_convergence(reasoning: str) -> bool:
    lowered = (reasoning or "").lower()
    return any(
        marker in lowered
        for marker in (
            "i now have enough to conclude",
            "enough to conclude",
            "all evidence converges",
            "convergence criteria are satisfied",
            "i can now conclude",
            "root cause is identified",
        )
    )


async def call_llm_analysis(state: AgentState, llm_with_tools) -> dict:
    """
    调用 LLM 分析节点，根据收集到的 vmcore 信息进行智能分析。

    此节点接收前序节点收集的诊断信息，通过 LLM 进行分析并决定：
    1. 是否需要执行更多 crash 命令获取详细信息
    2. 是否已有足够信息给出诊断结论

    Args:
        state: AgentState，包含历史消息和上下文
        llm_with_tools: 绑定了工具的 LLM 实例

    Returns:
        dict: 包含 messages、error 和 step_count 的状态更新
    """
    # 计算当前步数（之前的累加值 + 本次的 1）
    current_step = state.get("step_count", 0)

    logger.info(f"Starting {llm_analysis_node} node execution (step {current_step})...")
    curr_token_usage = 0

    # 准备系统消息，包含诊断知识库和输出格式
    # 检查是否是最后一步 (LangGraph recursion_limit 触发前)
    # 如果是最后一步，要求 LLM 停止工具调用并给出终止响应；该响应可以是有界的非结论总结
    is_last_step = state.get("is_last_step", False)
    if is_last_step:
        logger.warning(
            f"Agent reached the last step (is_last_step=True). Forcing a terminal response with no further tool calls."
        )

    system_message = build_analysis_system_prompt(
        state,
        is_last_step=is_last_step,
    )

    # 压缩消息历史后再发送给 LLM，避免 reasoning_content 累积和大工具输出导致 token 暴增
    compressed_messages = compress_messages_for_llm(state["messages"])
    messages_to_send = [SystemMessage(content=system_message), *compressed_messages]
    adaptive_max_tokens = compute_adaptive_max_tokens(messages_to_send)

    logger.info(
        f"Prepared messages for LLM analysis (step {current_step}): {[type(m).__name__ for m in messages_to_send]} with system prompt length {len(system_message)} and adaptive max_tokens {adaptive_max_tokens}"
    )

    # 这段代码既不是单纯让 LLM 终止推理，也不是单纯让它继续推理。它的真实作用是防止 agent 空转，并根据 is_last_step 决定：
    # is_last_step=false：优先推动继续诊断，必要时允许有边界地收口
    # is_last_step=true：强制停止继续查工具，要求输出终态结果
    last_msg = messages_to_send[-1] if messages_to_send else None
    if isinstance(last_msg, AIMessage) and not last_msg.tool_calls:
        last_step = None
        try:
            raw = (
                last_msg.content
                if isinstance(last_msg.content, str)
                else json.dumps(last_msg.content) if last_msg.content is not None else ""
            )
            last_step = VMCoreAnalysisStep.model_validate_json(raw)
        except Exception:
            last_step = None

        logger.warning(
            "Last message was an AIMessage without tool calls. Injecting HumanMessage to force progress."
        )
        messages_to_send.append(
            HumanMessage(
                content=(
                    (
                        "Your previous response already states that the evidence is sufficient to conclude. "
                        "Do NOT request more tools. Convert the current evidence into a final structured conclusion now. "
                        "If your remaining blocker is only gate bookkeeping, emit the specific gate updates needed to close the required gates, set action=null, set is_conclusive=true, and provide final_diagnosis. "
                        if last_step is not None
                        and last_step.root_cause_class not in {None, "unknown"}
                        and _reasoning_signals_convergence(last_step.reasoning)
                        else "Your previous response neither invoked any tools nor provided a valid terminal state. "
                        "You cannot remain in this state. "
                    )
                    + (
                        "This is the last step: do not call tools; either return a conclusive result with final_diagnosis, "
                        "or return a bounded non-conclusive result with action=null and explicit verification gaps."
                        if is_last_step
                        else "Please EITHER call a compliant tool out of the available options to gather more information, "
                        "OR, if no further tool can reduce uncertainty, return a bounded non-conclusive result with "
                        "action=null and explicit verification gaps. Set 'is_conclusive'=true with final_diagnosis "
                        "ONLY if the convergence criteria are actually satisfied."
                    )
                )
            )
        )

    # 结构化输出，设置 include_raw=True 以获取 token 消耗等元数据
    llm_analysis = llm_with_tools.bind(
        max_tokens=adaptive_max_tokens
    ).with_structured_output(
        VMCoreLLMAnalysisStep, method="json_mode", include_raw=True
    )
    try:
        # 使用 include_raw=True 后，ainvoke 返回包含 'parsed' 和 'raw' 的字典
        output_data = await ainvoke_with_retry(llm_analysis, messages_to_send)
        if output_data is None:
            logger.error("LLM output_data is None")
            raise ValueError("LLM returned None output")
        llm_step = cast(VMCoreLLMAnalysisStep, output_data["parsed"])
        raw_message = cast(AIMessage, output_data["raw"])

        # 获取并记录 token 消耗数量
        usage_metadata = getattr(raw_message, "usage_metadata", {}) or {}
        curr_token_usage = usage_metadata.get("total_tokens", 0)

        # 检查解析结果是否为空
        if llm_step is None:
            # 尝试修复常见的 JSON 格式错误
            logger.warning(
                "LLM output is empty or could not be parsed. Attempting to repair JSON..."
            )
            reasoning = raw_message.additional_kwargs.get("reasoning_content", "")
            content_str, reasoning_fallback = select_analysis_content(
                raw_message.content,
                reasoning,
            )
            logger.debug(
                f"use reasoning content: '{reasoning[:50] if reasoning else ''}' to attempt JSON repair"
            )

            if reasoning_fallback:
                logger.warning(
                    "Content is empty and reasoning_content is plain text (no JSON). "
                    "Routing to structure_reasoning_node for structuring."
                )
                return {
                    "step_count": 1,
                    "token_usage": curr_token_usage,
                    "reasoning_to_structure": reasoning_fallback,
                    "reasoning_additional_kwargs": raw_message.additional_kwargs.copy(),
                    "error": None,
                }

            if content_str:
                llm_step = repair_structured_output(
                    content_str,
                    model_class=VMCoreLLMAnalysisStep,
                )

            if llm_step is None:
                if reasoning and len(reasoning) > 50:
                    logger.warning(
                        "JSON repair failed but reasoning_content available. "
                        "Routing to structure_reasoning_node for structuring."
                    )
                    return {
                        "step_count": 1,
                        "token_usage": curr_token_usage,
                        "reasoning_to_structure": reasoning,
                        "reasoning_additional_kwargs": raw_message.additional_kwargs.copy(),
                        "error": None,
                    }

                parsing_error = output_data.get("parsing_error")
                error_msg = f"Failed to parse LLM output. Raw content: {repr(raw_message.content)}"
                if parsing_error:
                    error_msg += f". Parsing error: {parsing_error}"
                logger.error(error_msg)
                raise ValueError(error_msg)

        llm_step = apply_executor_consistency_audit(
            llm_step,
            cast(dict[str, Any], state),
            log_prefix=llm_analysis_node,
        )

        # 记录 response
        analysis_result, managed_updates = project_managed_analysis_step(
            llm_step,
            cast(dict[str, Any], state),
            original_reasoning=raw_message.additional_kwargs.get(
                "reasoning_content", ""
            )
            or str(raw_message.content),
        )

        logger.debug(
            f"LLM Analysis Result: {analysis_result.model_dump_json(indent=2)}"
        )

        # 手动构造 AIMessage 以便 edges.py 识别路由。
        # 如果 LLM 决定调用工具 (action 不为空)，我们需要手动填充 tool_calls
        # 安全屏障：当 is_last_step=True 时，强制清除 action，阻止生成 tool_calls。
        # 最后一步允许 bounded non-conclusive 结束，但不允许继续请求工具。
        tool_calls = build_tool_calls(analysis_result, is_last_step=is_last_step)

        # 将结构化后的对象序列化存入 content，并携带调用的工具信息
        # 必须保留 additional_kwargs 中的 reasoning_content，否则下一轮对话 DeepSeek-Reasoner 会报错 (Error 400)
        # DeepSeek-Reasoner 模式下，之前的 assistant 消息必须包含 reasoning_content
        # 如果 reasoning_content 存在，日志记录 reasoning_content 的前 100 字符以供调试

        reasoning_content = raw_message.additional_kwargs.get("reasoning_content")
        if reasoning_content:
            logger.debug(f"reasoning_content: {reasoning_content[:50]}...")
        else:
            logger.warning(
                f"No reasoning_content found in additional_kwargs. additional_kwargs keys: {raw_message.additional_kwargs.keys()}"
            )

        response = AIMessage(
            content=analysis_result.model_dump_json(),
            tool_calls=tool_calls,
            additional_kwargs=raw_message.additional_kwargs.copy(),
        )

    except Exception as e:
        logger.error(f"Error during LLM analysis: {e}", exc_info=True)
        return {
            "step_count": 1,
            "token_usage": curr_token_usage,
            "error": {
                "message": str(e),
                "node": llm_analysis_node,
                "is_error": True,
            },
        }

    return {
        "step_count": 1,
        "token_usage": curr_token_usage,
        "messages": [response],
        **managed_updates,
        "error": None,
    }


async def structure_reasoning_content(state: AgentState, structured_llm) -> dict:
    """
    使用 deepseek-chat 模型将 DeepSeek-Reasoner 的纯文本 reasoning_content 结构化为 VMCoreAnalysisStep。

    当 Reasoner 模型返回空 content 但有纯文本 reasoning_content 时，
    此节点接收该文本并通过 Chat 模型将其转换为结构化的分析步骤。

    Args:
        state: AgentState，包含 reasoning_to_structure 和 reasoning_additional_kwargs
        structured_llm: deepseek-chat LLM 实例

    Returns:
        dict: 包含 messages、reasoning_to_structure(清空) 等状态更新
    """
    reasoning = state.get("reasoning_to_structure", "")
    original_kwargs = state.get("reasoning_additional_kwargs", {}) or {}
    current_step = state.get("step_count", 0)
    is_last_step = state.get("is_last_step", False)

    logger.info(
        f"Starting {structure_reasoning_node} node execution (step {current_step})..."
    )
    logger.debug(
        f"Reasoning to structure (first 200 chars): {(reasoning or '')[:200]}..."
    )

    curr_token_usage = 0

    # 构建简化结构化提示（只提取核心字段）

    force_conclusion = build_structure_reasoning_force_conclusion(
        is_last_step=is_last_step
    )

    system_prompt = simplified_structure_reasoning_prompt().format(
        current_step=current_step,
        force_conclusion=force_conclusion,
    )
    system_prompt += "\n\n" + build_executor_state_section(state)

    # 只发送最小上下文：系统提示 + 待结构化的 reasoning 文本
    messages_to_send = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=reasoning),
    ]
    adaptive_max_tokens = compute_adaptive_max_tokens(
        messages_to_send,
        # default_max_tokens: 上下文充裕时允许的最大输出 token 数（上限）。
        # 此节点只做"文本 reasoning → 结构化 JSON"的简单转换，且用的是
        # deepseek-chat（非 Reasoner），输出远小于完整分析节点，8192 足够。
        default_max_tokens=8192,
        # min_max_tokens: 当输入消息逼近上下文上限时，输出预算可压缩到的下限（地板值）。
        # 2048 保证即便上下文紧张，也至少能产出一份有意义的结论，而非空输出。
        min_max_tokens=2048,
        # safety_margin_tokens: 预留的安全余量（token）。
        # 上下文占用是按 approx_chars_per_token≈2.0 粗估的，存在误差；
        # 预留 2048 兜底，避免估算偏差导致请求超过 context_limit 报 HTTP 400。
        safety_margin_tokens=2048,
    )

    chat_with_structured = structured_llm.bind(
        max_tokens=adaptive_max_tokens
    ).with_structured_output(
        VMCoreLLMAnalysisStep, method="json_mode", include_raw=True
    )

    try:
        output_data = await ainvoke_with_retry(chat_with_structured, messages_to_send)
        if output_data is None:
            raise ValueError(
                f"ainvoke_with_retry returned None for structured output. "
                f"Reasoning length: {len(reasoning or '')} chars."
            )
        llm_step = cast(VMCoreLLMAnalysisStep, output_data["parsed"])
        raw_chat_message = cast(AIMessage, output_data["raw"])

        usage_metadata = getattr(raw_chat_message, "usage_metadata", {}) or {}
        curr_token_usage = usage_metadata.get("total_tokens", 0)

        if llm_step is None:
            content = raw_chat_message.content
            content_str = content if isinstance(content, str) else json.dumps(content)
            llm_step = repair_structured_output(
                content_str,
                model_class=VMCoreLLMAnalysisStep,
                log_prefix=structure_reasoning_node,
            )

        if llm_step is None:
            raise ValueError(
                f"Chat model failed to structure reasoning. "
                f"Raw: {repr(raw_chat_message.content[:200])}"
            )

        llm_step = apply_executor_consistency_audit(
            llm_step,
            cast(dict[str, Any], state),
            log_prefix=structure_reasoning_node,
        )

        # 强制覆盖 step_id 为当前实际步数，chat 模型可能输出错误值
        llm_step.step_id = current_step

        analysis_result, managed_updates = project_managed_analysis_step(
            llm_step,
            cast(dict[str, Any], state),
            original_reasoning=reasoning or "",
        )

        logger.info(
            f"structure_reasoning_node: Successfully structured reasoning content. "
            f"LLM Analysis Result: {analysis_result.model_dump_json(indent=2)}"
        )

        # 构建 tool_calls（与 call_llm_analysis 相同逻辑）
        tool_calls = build_tool_calls(
            analysis_result,
            is_last_step=is_last_step,
            log_prefix=structure_reasoning_node,
        )

        # 使用原始的 additional_kwargs（含 reasoning_content）以确保
        # 下一轮 DeepSeek-Reasoner 调用时 assistant 消息包含 reasoning_content
        response = AIMessage(
            content=analysis_result.model_dump_json(),
            tool_calls=tool_calls,
            additional_kwargs=original_kwargs,
        )

    except Exception as e:
        logger.error(f"Error in structure_reasoning_node: {e}", exc_info=True)
        return {
            "step_count": 0,
            "token_usage": curr_token_usage,
            "reasoning_to_structure": None,
            "reasoning_additional_kwargs": None,
            "error": {
                "message": str(e),
                "node": structure_reasoning_node,
                "is_error": True,
            },
        }

    return {
        "step_count": 0,  # 步数已在 llm_analysis_node 中计入
        "token_usage": curr_token_usage,
        "messages": [response],
        "reasoning_to_structure": None,
        "reasoning_additional_kwargs": None,
        **managed_updates,
        "error": None,
    }
