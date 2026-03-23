#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# llm_runtime.py - LLM 运行时工具和重试机制模块
# Author: CalmWU
# Created: 2026-03-23

import asyncio

import openai
from langchain_core.messages import AIMessage, ToolMessage

from src.utils.logging import logger


async def ainvoke_with_retry(
    chain, messages: list, max_retries: int = 3, base_delay: float = 2.0
):
    """对 LLM ainvoke 调用进行指数退避重试，仅针对瞬态网络连接错误。"""
    for attempt in range(max_retries):
        try:
            return await chain.ainvoke(messages)
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                f"[retry] Transient API error on attempt {attempt + 1}/{max_retries}, "
                f"retrying in {delay:.0f}s: {exc}"
            )
            await asyncio.sleep(delay)


def compress_messages_for_llm(
    messages: list,
    max_tool_output_chars: int = 4000,
    old_reasoning_head: int = 200,
    old_reasoning_tail: int = 500,
    recent_ai_messages_to_keep: int = 2,
    recent_tool_messages_to_keep: int = 2,
) -> list:
    """
    在发送给 LLM 前对消息历史进行保守压缩，降低 token 消耗。

    策略：
    1. 保留最近几条 AIMessage 和 ToolMessage 的完整内容，尽量减少对当前推理链的干扰。
    2. 更早的 AIMessage 的 reasoning_content 保留头部 + 尾部，中间截断。
       - 头部（old_reasoning_head）：保留"当前在分析什么"的上下文
       - 尾部（old_reasoning_tail）：保留"结论和下一步决策"（推理链结论在尾部）
       ⚠️ DeepSeek-Reasoner API 要求所有 assistant 消息必须包含 reasoning_content 字段，
       不可删除、不可设为 None，否则返回 400 错误。
    3. 对更早的 ToolMessage，仅在超过 max_tool_output_chars 时才截断。

    此函数不修改 AgentState，仅返回压缩后的副本用于当次 LLM 调用。
    """
    ai_msg_indices = [i for i, msg in enumerate(messages) if isinstance(msg, AIMessage)]
    tool_msg_indices = [
        i for i, msg in enumerate(messages) if isinstance(msg, ToolMessage)
    ]
    recent_ai_indices = set(ai_msg_indices[-recent_ai_messages_to_keep:])
    recent_tool_indices = set(tool_msg_indices[-recent_tool_messages_to_keep:])
    keep_threshold = old_reasoning_head + old_reasoning_tail

    def truncate_middle(text: str, head_chars: int, tail_chars: int) -> str:
        keep_chars = head_chars + tail_chars
        if keep_chars <= 0 or len(text) <= keep_chars:
            return text

        omitted = len(text) - keep_chars
        return (
            text[:head_chars]
            + f"\n...[{omitted} chars truncated]...\n"
            + text[-tail_chars:]
        )

    compressed = []
    truncated_reasoning_count = 0
    truncated_tool_count = 0
    reasoning_chars_before = 0
    reasoning_chars_after = 0
    tool_chars_before = 0
    tool_chars_after = 0

    for index, msg in enumerate(messages):
        if isinstance(msg, AIMessage) and index not in recent_ai_indices:
            reasoning_content = msg.additional_kwargs.get("reasoning_content", "")
            if isinstance(reasoning_content, str):
                reasoning_chars_before += len(reasoning_content)
            if reasoning_content and len(reasoning_content) > keep_threshold:
                new_kwargs = dict(msg.additional_kwargs)
                new_kwargs["reasoning_content"] = truncate_middle(
                    reasoning_content,
                    old_reasoning_head,
                    old_reasoning_tail,
                )
                msg = msg.model_copy(update={"additional_kwargs": new_kwargs})
                truncated_reasoning_count += 1
            if isinstance(msg.additional_kwargs.get("reasoning_content"), str):
                reasoning_chars_after += len(msg.additional_kwargs["reasoning_content"])
        elif (
            isinstance(msg, ToolMessage)
            and index not in recent_tool_indices
            and isinstance(msg.content, str)
            and len(msg.content) > max_tool_output_chars
        ):
            tool_chars_before += len(msg.content)
            tool_head_chars = max_tool_output_chars * 3 // 5
            tool_tail_chars = max_tool_output_chars - tool_head_chars
            truncated_content = truncate_middle(
                msg.content,
                tool_head_chars,
                tool_tail_chars,
            )
            msg = msg.model_copy(update={"content": truncated_content})
            truncated_tool_count += 1
            tool_chars_after += len(msg.content)
        elif (
            isinstance(msg, ToolMessage)
            and index not in recent_tool_indices
            and isinstance(msg.content, str)
        ):
            tool_chars_before += len(msg.content)
            tool_chars_after += len(msg.content)
        compressed.append(msg)

    if truncated_reasoning_count or truncated_tool_count:
        reasoning_saved = reasoning_chars_before - reasoning_chars_after
        tool_saved = tool_chars_before - tool_chars_after
        logger.info(
            f"[compress] truncated reasoning_content in {truncated_reasoning_count} old AIMessages "
            f"(head={old_reasoning_head}+tail={old_reasoning_tail} chars, "
            f"before={reasoning_chars_before}, after={reasoning_chars_after}, saved={reasoning_saved}), "
            f"truncated {truncated_tool_count} old ToolMessages (limit={max_tool_output_chars}, "
            f"before={tool_chars_before}, after={tool_chars_after}, saved={tool_saved}, "
            f"kept recent ai/tool messages full: {recent_ai_messages_to_keep}/{recent_tool_messages_to_keep})"
        )
    return compressed
