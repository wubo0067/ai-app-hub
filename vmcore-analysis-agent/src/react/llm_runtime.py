#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# llm_runtime.py - LLM 运行时工具和重试机制模块
# Author: CalmWU
# Created: 2026-03-23

import asyncio
import math

import openai
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.utils.logging import logger

REASONER_CONTEXT_LIMIT_TOKENS = 131_072
DEFAULT_REASONER_MAX_TOKENS = 48_000
MIN_REASONER_MAX_TOKENS = 4_096
REASONER_TOKEN_SAFETY_MARGIN = 8_192
APPROX_CHARS_PER_TOKEN = 3.0


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


# TODO: 摘要化压缩 (Summarization/Compression), 利用一个更小、更便宜的模型（如 GPT-4o-mini 或本地的 Llama-3-8B）对过长的 ToolMessage 进行预处理。
def compress_messages_for_llm(
    messages: list,
    max_tool_output_chars: int = 4000,  # 较早的 ToolMessage 内容的最大字符数限制
    recent_tool_messages_to_keep: int = 2,  # 需要应用较宽松限制的最近 ToolMessage 的数量
    max_recent_tool_output_chars: int = 12000,  # 最近 ToolMessage 内容的最大字符数限制
) -> list:
    """
    在发送给 LLM 前对消息历史进行保守压缩，降低 token 消耗。

     策略：
     1. 所有 AIMessage 一律原样保留，尤其禁止改写 reasoning_content。
    2. 最近几条 ToolMessage 默认保留更多内容，但若单条过大仍会压缩到上限。
    3. 对更早的 ToolMessage，当其返回内容超过 max_tool_output_chars 时，截断其中间部分。

    此函数不修改 AgentState，仅返回压缩后的副本用于当次 LLM 调用。
    """
    # 获取所有 ToolMessage 在原始消息列表中的索引
    tool_msg_indices = [
        i for i, msg in enumerate(messages) if isinstance(msg, ToolMessage)
    ]
    # 获取最近需要保留更多信息的 ToolMessage 索引集合
    recent_tool_indices = _recent_index_set(
        tool_msg_indices, recent_tool_messages_to_keep
    )

    def truncate_middle(text: str, head_chars: int, tail_chars: int) -> str:
        # 计算需要保留的总字符数
        keep_chars = head_chars + tail_chars
        # 如果保留字符数小于等于 0 或文本长度小于等于保留字符数，则直接返回原文本
        if keep_chars <= 0 or len(text) <= keep_chars:
            return text

        # 计算被省略的字符数
        omitted = len(text) - keep_chars
        # 返回截取后的文本：头部 + 系统日志标记 + 尾部
        return (
            text[:head_chars]
            + f"\n\n[SYSTEM LOG: {omitted} characters from this older tool execution have been pruned to save context window]\n\n"
            + text[-tail_chars:]
        )

    # 存储压缩后的消息列表
    compressed = []
    # 统计被截断的 ToolMessage 数量
    truncated_tool_count = 0
    # 统计被截断的近期 ToolMessage 数量
    truncated_recent_tool_count = 0
    # 统计压缩前 ToolMessage 的总字符数
    tool_chars_before = 0
    # 统计压缩后 ToolMessage 的总字符数
    tool_chars_after = 0

    # 遍历消息列表，对 ToolMessage 进行压缩处理
    for index, msg in enumerate(messages):
        # 如果不是 ToolMessage 或者内容不是字符串，则直接添加到压缩列表中
        if not isinstance(msg, ToolMessage) or not isinstance(msg.content, str):
            compressed.append(msg)
            continue

        # 累加压缩前的字符数
        tool_chars_before += len(msg.content)

        # 根据消息索引判断使用哪种字符限制：近期消息使用较宽松的限制，其他消息使用较严格限制
        tool_limit = (
            max_recent_tool_output_chars
            if index in recent_tool_indices
            else max_tool_output_chars
        )
        # 如果当前消息内容长度超过了对应限制，则进行截断处理
        if len(msg.content) > tool_limit:
            # 计算头部保留字符数（占限制的 3/5）
            tool_head_chars = tool_limit * 3 // 5
            # 计算尾部保留字符数（占限制的 2/5）
            tool_tail_chars = tool_limit - tool_head_chars
            # 使用中间截断函数处理消息内容
            truncated_content = truncate_middle(
                msg.content,
                tool_head_chars,
                tool_tail_chars,
            )
            # 创建新的 ToolMessage 对象，更新其内容为截断后的内容
            msg = msg.model_copy(update={"content": truncated_content})
            # 增加被截断的消息计数
            truncated_tool_count += 1
            # 如果是近期消息，增加近期截断计数
            if index in recent_tool_indices:
                truncated_recent_tool_count += 1
            # 累加压缩后的字符数
            tool_chars_after += len(msg.content)
            # 将处理后的消息添加到压缩列表
            compressed.append(msg)
        else:
            # 如果未超过限制，直接累加字符数并添加消息到压缩列表
            tool_chars_after += len(msg.content)
            compressed.append(msg)

    # 如果有被截断的消息，记录压缩统计信息
    if truncated_tool_count:
        tool_saved = tool_chars_before - tool_chars_after
        logger.info(
            f"[compress] truncated {truncated_tool_count} ToolMessages (older_limit={max_tool_output_chars}, "
            f"before={tool_chars_before}, after={tool_chars_after}, saved={tool_saved}, "
            f"kept recent tool messages full: {recent_tool_messages_to_keep - truncated_recent_tool_count}, "
            f"bounded recent tool messages: {truncated_recent_tool_count}, recent_limit={max_recent_tool_output_chars})"
        )
    return compressed


def estimate_message_char_budget(messages: list) -> int:
    """
    估算消息列表的字符预算

    该函数遍历消息列表，计算所有消息内容的字符总数，包括普通内容和 AI 消息的推理内容，
    用于后续计算上下文窗口中已使用的字符预算。

    Args:
        messages (list): 消息对象列表，通常包含 SystemMessage、HumanMessage、
                         AIMessage、ToolMessage 等类型的消息

    Returns:
        int: 消息列表中所有内容的字符总数
    """
    # 初始化总字符数为 0
    total_chars = 0

    # 遍历消息列表中的每条消息
    for message in messages:
        # 获取消息的 content 属性，如果不存在则默认为空字符串
        content = getattr(message, "content", "")
        # 判断 content 是否为字符串类型
        if isinstance(content, str):
            # 如果是字符串，直接计算其长度并加入总字符数
            total_chars += len(content)
        else:
            # 如果不是字符串，将其转换为字符串后计算长度
            total_chars += len(str(content))

        # 检查当前消息是否为 AI 消息类型
        if isinstance(message, AIMessage):
            # 从 AI 消息的额外参数中获取推理内容
            reasoning = message.additional_kwargs.get("reasoning_content")
            # 检查推理内容是否为字符串类型
            if isinstance(reasoning, str):
                # 如果是字符串，将其长度加入总字符数
                total_chars += len(reasoning)

        # 检查当前消息是否为系统消息、人类消息或工具消息类型
        if isinstance(message, (SystemMessage, HumanMessage, ToolMessage)):
            # 这些类型的消息已处理过基本内容，跳过后续特殊处理
            continue

    # 返回计算得到的总字符数
    return total_chars


def compute_adaptive_max_tokens(
    messages: list,
    *,
    default_max_tokens: int = DEFAULT_REASONER_MAX_TOKENS,
    context_limit_tokens: int = REASONER_CONTEXT_LIMIT_TOKENS,
    min_max_tokens: int = MIN_REASONER_MAX_TOKENS,
    safety_margin_tokens: int = REASONER_TOKEN_SAFETY_MARGIN,
    approx_chars_per_token: float = APPROX_CHARS_PER_TOKEN,
) -> int:
    # 计算消息列表中所有内容的字符预算估计值，并转换为 token 数
    approx_message_tokens = math.ceil(
        estimate_message_char_budget(messages) / approx_chars_per_token
    )
    # 计算可用的 completion tokens 数量：上下文限制 - 消息 tokens - 安全边距
    available_completion_tokens = (
        context_limit_tokens - approx_message_tokens - safety_margin_tokens
    )

    # 如果可用的 completion tokens 小于等于最小最大 token 数，返回最小值
    if available_completion_tokens <= min_max_tokens:
        return min_max_tokens

    # 返回默认最大 token 数和可用 completion tokens 中的较小值
    return min(default_max_tokens, available_completion_tokens)


def _recent_index_set(indices: list[int], keep_count: int) -> set[int]:
    if keep_count <= 0:
        return set()
    return set(indices[-keep_count:])
