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
# 上下文以中文/推理文本为主时，字符与 token 的比值更低（约 1.5~2.0）。
# 采用更保守的 2.0 估算，避免低估上下文占用导致 context 超限（HTTP 400）。
APPROX_CHARS_PER_TOKEN = 2.0

# 证据型 crash 工具：其中间输出（日志行、slab 槽位表、反汇编指令、结构体字段）
# 是诊断的核心证据，享受比普通工具输出更宽松的截断上限，降低"中间截断丢失关键证据"
# 对推理的影响；但宽松上限来自一个全局预算（见 EVIDENCE_BUDGET_CHARS），并非无条件放宽。
EVIDENCE_TOOL_COMMANDS: frozenset[str] = frozenset(
    {
        "bt",
        "log",
        "kmem",
        "struct",
        "dis",
        "search",
        "list",
        "foreach",
        "rd",
        "p",
        "dev",
        "files",
        "mount",
        "vm",
        "pte",
        "whatis",
        "waitq",
        "run_script",
    }
)
EVIDENCE_TOOL_LIMIT_CHARS = 24_000
# nodes.py 去重后返回的 DEDUP 消息前缀：内容为完整缓存输出，是 LLM 主动重请求
# 以获得完整证据的结果，绝不截断。
DEDUP_PREFIX = "[DEDUP]"

# 最近 DEDUP_FULL_PRESERVE_COUNT 条 DEDUP 消息享受"近似完整保留"（仅受 DEDUP_HARD_CAP_CHARS
# 硬上限约束，不参与常规的证据预算分配），超出此数量的旧 DEDUP 消息退化为按证据型命令的
# 预算分配逻辑处理，避免历史 DEDUP 永久占据上下文。
DEDUP_FULL_PRESERVE_COUNT = 2

# 单条 DEDUP 消息的硬上限：即便是最近 DEDUP_FULL_PRESERVE_COUNT 条内、享受"完整保留"待遇的
# DEDUP 消息，也不能无限增长——DEDUP 恰恰是"原始输出最大"的那批消息，若无个体上限，1~2 条
# 超大 DEDUP 就足以把总上下文预算撑爆，进而触发下方更粗粒度的紧急降级，反而抹掉了对
# "最近证据"的精细化保留。此上限明显宽于 EVIDENCE_TOOL_LIMIT_CHARS，体现 DEDUP 消息
# "LLM 主动重请求换回完整证据"的优先级更高，但仍然是有底线的。
DEDUP_HARD_CAP_CHARS = 40_000

# 证据型消息（含超出 DEDUP_FULL_PRESERVE_COUNT 的旧 DEDUP 消息）的全局字符预算上限。
# 预算按"离当前最近"优先分配（见 compress_messages_for_llm 中的逆序预分配），
# 超出预算后，越久远的证据消息越先被降级到 FALLBACK_EVIDENCE_LIMIT_CHARS。
EVIDENCE_BUDGET_CHARS = 80_000

# 证据型消息预算耗尽后的降级截断上限。
FALLBACK_EVIDENCE_LIMIT_CHARS = 8_000


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


def _assign_evidence_tiers(
    messages: list,
    tool_msg_indices: list[int],
    recent_dedup_positions: set[int],
    evidence_budget_chars: int,
    evidence_tool_limit_chars: int,
    fallback_evidence_limit_chars: int,
) -> dict[int, int]:
    """
    为证据型 ToolMessage（含超出 DEDUP_FULL_PRESERVE_COUNT 的旧 DEDUP 消息）预分配字符档位。

    关键点：按"离当前最近"优先分配，而不是按消息在历史中出现的先后顺序分配。
    做法是从 tool_msg_indices 的末尾（最新）往前（最旧）扫描，用一个全局预算
    evidence_budget_chars 依次满足每条证据消息的宽松档 evidence_tool_limit_chars；
    一旦预算耗尽，从这里开始往更旧的方向，所有后续（更旧的）证据消息都降级到
    fallback_evidence_limit_chars。

    这样可以保证：无论会话进行到多少轮，最新的若干条证据消息始终优先拿到宽松档，
    而不会出现"早期证据消息因为先被遍历到而永久占用预算，后续更贴近当前推理的
    证据消息反而长期被压缩"的反向优先级问题。

    最近的 DEDUP 消息（在 recent_dedup_positions 中）不参与此预算分配——它们由
    调用方按 DEDUP_HARD_CAP_CHARS 单独处理，避免占用/挤压其他证据消息的预算。

    返回：{消息索引: 分配到的字符上限}，仅包含被判定为"证据型"的消息索引。
    """
    tiers: dict[int, int] = {}
    budget_used = 0
    # 从最新（列表末尾）到最旧（列表开头）逆序扫描
    for i in reversed(tool_msg_indices):
        msg = messages[i]
        if not isinstance(msg.content, str):
            continue
        is_dedup_i = msg.content.startswith(DEDUP_PREFIX)
        if is_dedup_i and i in recent_dedup_positions:
            # 最近 DEDUP 消息不占用证据预算，交给硬上限逻辑单独处理
            continue
        is_evidence_i = is_dedup_i or msg.name in EVIDENCE_TOOL_COMMANDS
        if not is_evidence_i:
            continue
        limit = (
            evidence_tool_limit_chars
            if budget_used < evidence_budget_chars
            else fallback_evidence_limit_chars
        )
        tiers[i] = limit
        # 按实际会占用的字符数（截断后）计入预算，而非原始长度
        budget_used += min(len(msg.content), limit)
    return tiers


# TODO: 摘要化压缩 (Summarization/Compression), 利用一个更小、更便宜的模型（如 GPT-4o-mini 或本地的 Llama-3-8B）对过长的 ToolMessage 进行预处理。
def compress_messages_for_llm(
    messages: list,
    max_tool_output_chars: int = 4000,  # 较早的 ToolMessage 内容的最大字符数限制
    recent_tool_messages_to_keep: int = 2,  # 需要应用较宽松限制的最近 ToolMessage 的数量
    max_recent_tool_output_chars: int = 12000,  # 最近 ToolMessage 内容的最大字符数限制
    evidence_tool_limit_chars: int = EVIDENCE_TOOL_LIMIT_CHARS,  # 证据型命令 ToolMessage 的字符数限制
) -> list:
    """
    在发送给 LLM 前对消息历史进行保守压缩，降低 token 消耗。

    策略：
    1. 所有 AIMessage 一律原样保留，尤其禁止改写 reasoning_content。
    2. 最近几条 ToolMessage 默认保留更多内容，但若单条过大仍会压缩到上限。
    3. 证据型命令（bt/log/kmem/struct/dis 等）的 ToolMessage 享受比普通消息更宽松的
       evidence_tool_limit_chars 上限，但这个"宽松待遇"来自一个全局预算
       EVIDENCE_BUDGET_CHARS，且预算按"离当前最近"优先分配（逆序预分配，见下方
       _assign_evidence_tiers）：越靠近当前轮次的证据消息越优先拿到宽松档，
       预算耗尽后，越久远的证据消息越先降级到 FALLBACK_EVIDENCE_LIMIT_CHARS。
       同一消息在同一次调用内使用同一上限，保证 LLM 本轮看到的截断结果一致。
    4. DEDUP 消息（LLM 主动重请求同一命令得到的完整缓存输出）中，最近
       DEDUP_FULL_PRESERVE_COUNT 条享受远高于普通证据消息的上限
       （DEDUP_HARD_CAP_CHARS），但并非无限——单条硬上限存在，避免 1~2 条超大
       DEDUP 输出独自撑爆总预算；更早的 DEDUP 消息退化为按证据型消息的预算分配处理。
    5. 对超过限制的 ToolMessage，截断其中间部分，并在截断标记中提示可重新执行
       原命令获取完整输出。
    6. 兜底：以上步骤压缩后，若消息总字符数估算仍超出上下文预算，对所有证据型/
       DEDUP 消息做一次不分新旧的紧急降级（压到 max_tool_output_chars），
       确保绝不把明显超限的请求发给 LLM。这是最后一道防线，会牺牲掉第 3/4 点
       原本想保留的"越新越详细"的精细化处理——出现这种情况本身就说明前面的
       预算参数需要调低，值得在日志里关注。

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

    # 识别所有 DEDUP 消息位置，仅最近 DEDUP_FULL_PRESERVE_COUNT 条享受完整保留
    dedup_positions = [
        i
        for i in tool_msg_indices
        if isinstance(messages[i].content, str)
        and messages[i].content.startswith(DEDUP_PREFIX)
    ]
    recent_dedup_positions = (
        set(dedup_positions[-DEDUP_FULL_PRESERVE_COUNT:])
        if dedup_positions
        else set()
    )

    # 逆序（从新到旧）为证据型消息预分配字符档位，确保预算优先满足离当前最近的消息
    evidence_tiers = _assign_evidence_tiers(
        messages,
        tool_msg_indices,
        recent_dedup_positions,
        EVIDENCE_BUDGET_CHARS,
        evidence_tool_limit_chars,
        FALLBACK_EVIDENCE_LIMIT_CHARS,
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
            + f"\n\n[SYSTEM LOG: {omitted} characters of this tool output have been pruned "
            f"(middle section) to save context window. If the pruned portion may contain "
            f"critical evidence, re-invoke the same command to obtain its full output.]\n\n"
            + text[-tail_chars:]
        )

    # 存储压缩后的消息列表
    compressed = []
    # 统计被截断的 ToolMessage 数量
    truncated_tool_count = 0
    # 统计被截断的近期 ToolMessage 数量
    truncated_recent_tool_count = 0
    # 统计被截断的证据型命令 ToolMessage 数量（含降级为证据处理的旧 DEDUP 消息）
    truncated_evidence_tool_count = 0
    # 统计被硬上限截断的"最近 DEDUP"消息数量
    truncated_recent_dedup_count = 0
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

        # 判断是否为证据型消息（evidence 命令 或 DEDUP 消息）
        is_dedup = msg.content.startswith(DEDUP_PREFIX)
        if is_dedup and index in recent_dedup_positions:
            # 最近 DEDUP 消息：享受远高于普通证据消息的上限，但仍设硬上限
            # （DEDUP_HARD_CAP_CHARS），避免单条超大 DEDUP 输出独自撑爆总预算。
            if len(msg.content) > DEDUP_HARD_CAP_CHARS:
                dedup_head_chars = DEDUP_HARD_CAP_CHARS * 3 // 5
                dedup_tail_chars = DEDUP_HARD_CAP_CHARS - dedup_head_chars
                truncated_content = truncate_middle(
                    msg.content, dedup_head_chars, dedup_tail_chars
                )
                msg = msg.model_copy(update={"content": truncated_content})
                truncated_tool_count += 1
                truncated_recent_dedup_count += 1
            tool_chars_after += len(msg.content)
            compressed.append(msg)
            continue
        # 旧 DEDUP 消息（超出 DEDUP_FULL_PRESERVE_COUNT）：视为证据型，进入统一的
        # 预算分配流程，与普通证据命令一样按 evidence_tiers 处理。
        is_evidence = is_dedup or msg.name in EVIDENCE_TOOL_COMMANDS

        # 字符限制取二者中的较大值：
        # - 最近 ToolMessage 使用较宽松限制（近期证据权重更高）
        # - 证据型命令（含旧 DEDUP 消息）使用 _assign_evidence_tiers 预先分配好的档位
        #   （该档位已按"离当前最近优先"的原则分配，无需在此处再判断预算是否耗尽）
        tool_limit = max_tool_output_chars
        if index in recent_tool_indices:
            tool_limit = max(tool_limit, max_recent_tool_output_chars)
        if is_evidence:
            tool_limit = max(
                tool_limit, evidence_tiers.get(index, FALLBACK_EVIDENCE_LIMIT_CHARS)
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
            # 如果是证据型消息（含降级的旧 DEDUP），增加证据型截断计数
            if is_evidence:
                truncated_evidence_tool_count += 1
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
        evidence_full_tier_count = sum(
            1 for limit in evidence_tiers.values() if limit == evidence_tool_limit_chars
        )
        evidence_fallback_tier_count = sum(
            1
            for limit in evidence_tiers.values()
            if limit == FALLBACK_EVIDENCE_LIMIT_CHARS
        )
        logger.info(
            f"[compress] truncated {truncated_tool_count} ToolMessages (older_limit={max_tool_output_chars}, "
            f"before={tool_chars_before}, after={tool_chars_after}, saved={tool_saved}, "
            f"kept recent tool messages full: {recent_tool_messages_to_keep - truncated_recent_tool_count}, "
            f"bounded recent tool messages: {truncated_recent_tool_count}, recent_limit={max_recent_tool_output_chars}, "
            f"bounded evidence tool messages: {truncated_evidence_tool_count}, "
            f"evidence_full_tier={evidence_full_tier_count}, evidence_fallback_tier={evidence_fallback_tier_count}, "
            f"evidence_budget={EVIDENCE_BUDGET_CHARS}, "
            f"truncated_recent_dedup={truncated_recent_dedup_count}, dedup_hard_cap={DEDUP_HARD_CAP_CHARS})"
        )

    # --- 总量安全检查：压缩后若仍超上下文预算，对证据型消息执行紧急降级 ---
    total_compressed_chars = sum(
        len(m.content) if isinstance(m.content, str) else len(str(m.content))
        for m in compressed
    )
    context_char_limit = (
        REASONER_CONTEXT_LIMIT_TOKENS - REASONER_TOKEN_SAFETY_MARGIN
    ) * APPROX_CHARS_PER_TOKEN

    if total_compressed_chars > context_char_limit:
        logger.warning(
            f"[compress] total compressed chars ({total_compressed_chars}) exceeds "
            f"context budget ({context_char_limit:.0f}), applying emergency downgrade "
            f"for evidence/DEDUP messages to base limit ({max_tool_output_chars})"
        )
        emergency_compressed: list = []
        emergency_downgraded = 0
        for msg in compressed:
            if isinstance(msg, ToolMessage) and isinstance(msg.content, str):
                content = msg.content
                if (
                    msg.name in EVIDENCE_TOOL_COMMANDS
                    or content.startswith(DEDUP_PREFIX)
                ) and len(content) > max_tool_output_chars:
                    tool_head_chars = max_tool_output_chars * 3 // 5
                    tool_tail_chars = max_tool_output_chars - tool_head_chars
                    truncated_content = truncate_middle(
                        content, tool_head_chars, tool_tail_chars
                    )
                    msg = msg.model_copy(update={"content": truncated_content})
                    emergency_downgraded += 1
            emergency_compressed.append(msg)
        compressed = emergency_compressed
        logger.warning(
            f"[compress] emergency downgrade applied to {emergency_downgraded} messages"
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
    # 获取最近的索引集合，保留最后 keep_count 个索引
    return set(indices[-keep_count:])
