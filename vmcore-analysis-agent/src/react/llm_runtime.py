#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# llm_runtime.py - LLM 运行时工具和重试机制模块
# Author: CalmWU
# Created: 2026-03-23

import asyncio

import openai
from langchain_core.messages import ToolMessage

from src.utils.logging import logger


async def ainvoke_with_retry(
    chain, messages: list, max_retries: int = 3, base_delay: float = 2.0
):
    """对 LLM ainvoke 调用进行指数退避重试，仅针对瞬态网络连接错误。

    同时捕获 LengthFinishReasonError（reasoning_tokens 耗尽 max_tokens 导致
    content 为空），此类错误带有随机性（同样的 prompt 下次可能不会触发），
    因此也纳入重试范围。
    """
    for attempt in range(max_retries):
        try:
            return await chain.ainvoke(messages)
        except openai.LengthFinishReasonError as exc:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                f"[retry] LLM output hit max_tokens (reasoning exhausted budget) "
                f"on attempt {attempt + 1}/{max_retries}, retrying in {delay:.0f}s: {exc}"
            )
            await asyncio.sleep(delay)
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
    recent_tool_messages_to_keep: int = 2,
) -> list:
    """
    在发送给 LLM 前对消息历史进行保守压缩，降低 token 消耗。

     策略：
     1. 所有 AIMessage 一律原样保留，尤其禁止改写 reasoning_content。
     2. 保留最近几条 ToolMessage 的完整内容。
     3. 对更早的 ToolMessage，当其返回内容超过 max_tool_output_chars 时，截断其中间部分。

    此函数不修改 AgentState，仅返回压缩后的副本用于当次 LLM 调用。
    """
    tool_msg_indices = [
        i for i, msg in enumerate(messages) if isinstance(msg, ToolMessage)
    ]
    recent_tool_indices = _recent_index_set(
        tool_msg_indices,
        recent_tool_messages_to_keep,
    )

    def truncate_middle(text: str, head_chars: int, tail_chars: int) -> str:
        keep_chars = head_chars + tail_chars
        if keep_chars <= 0 or len(text) <= keep_chars:
            return text

        omitted = len(text) - keep_chars
        return (
            text[:head_chars]
            + f"\n\n[SYSTEM LOG: {omitted} characters from this older tool execution have been pruned to save context window]\n\n"
            + text[-tail_chars:]
        )

    compressed = []
    truncated_tool_count = 0
    tool_chars_before = 0
    tool_chars_after = 0

    for index, msg in enumerate(messages):
        if (
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
            compressed.append(msg)
        elif isinstance(msg, ToolMessage) and isinstance(msg.content, str):
            tool_chars_before += len(msg.content)
            tool_chars_after += len(msg.content)
            compressed.append(msg)
        else:
            compressed.append(msg)

    if truncated_tool_count:
        tool_saved = tool_chars_before - tool_chars_after
        logger.info(
            f"[compress] truncated {truncated_tool_count} old ToolMessages (limit={max_tool_output_chars}, "
            f"before={tool_chars_before}, after={tool_chars_after}, saved={tool_saved}, "
            f"kept recent tool messages full: {recent_tool_messages_to_keep})"
        )
    return compressed


def _recent_index_set(indices: list[int], keep_count: int) -> set[int]:
    if keep_count <= 0:
        return set()
    return set(indices[-keep_count:])
