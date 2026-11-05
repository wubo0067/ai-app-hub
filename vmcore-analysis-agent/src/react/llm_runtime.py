#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# llm_runtime.py - LLM 运行时工具和重试机制模块
# Author: CalmWU
# Created: 2026-03-23

import asyncio
import json
import math
from dataclasses import dataclass
from typing import Optional

import openai
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from src.utils.logging import logger

# 输出预算：thinking 模式必须覆盖 reasoning + 正文，不能再按 4,096 保底。
MIN_TEXT_OUTPUT_TOKENS = 4_096
MAX_TEXT_OUTPUT_TOKENS = 16_384
REASONER_TOKEN_SAFETY_MARGIN = 8_192
INITIAL_CHARS_PER_TOKEN = 1.5
REASONING_DIGEST_CHARS = 1_000

# 运行时校准值：初始用 1.5，后续根据 API usage 的 prompt_tokens 更新。
_CHARS_PER_TOKEN = INITIAL_CHARS_PER_TOKEN

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
# nodes.py 去重后判定无实质证据时返回的拒绝消息前缀。
# 此类消息不含历史输出，不应享受完整保留待遇，按普通短消息处理。
DEDUP_BLOCKED_PREFIX = "[DEDUP-BLOCKED]"

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


class ContextBudgetError(RuntimeError):
    """请求在压缩后仍无法放入上下文/输出预算。"""


@dataclass(frozen=True)
class LLMBudgetConfig:
    """一次 LLM 调用的上下文与输出预算配置。"""

    context_window: int = 524_288
    max_output_tokens: int = 48_000
    min_thinking_output_tokens: int = 40_960
    thinking_enabled: bool = True
    reasoning_effort: str = "max"
    safety_margin_tokens: int = REASONER_TOKEN_SAFETY_MARGIN
    min_output_tokens: int = MIN_TEXT_OUTPUT_TOKENS
    max_text_output_tokens: int = MAX_TEXT_OUTPUT_TOKENS
    reasoning_history_keep: int = 2
    chars_per_token: Optional[float] = None


@dataclass(frozen=True)
class OutputBudget:
    """预算规划结果；max_tokens=None 表示必须先压缩。"""

    max_tokens: Optional[int]
    thinking: bool
    reasoning_effort: str
    prompt_tokens: int
    prompt_chars: int
    must_compress: bool = False
    reason: str = ""


async def ainvoke_with_retry(
    chain, messages: list, max_retries: int = 3, base_delay: float = 2.0
):
    """对 LLM ainvoke 调用进行指数退避重试，仅针对瞬态网络连接错误。

    LengthFinishReasonError 表示输出预算已经耗尽，原样重试同一请求通常仍会失败，
    因此交给上层做一次有界降级重试（或快速失败），不在本函数内重试。
    """
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


def _truncate_middle(text: str, head_chars: int, tail_chars: int) -> str:
    """保留头尾、截断中间，并插入可操作的重新获取提示。"""
    keep_chars = head_chars + tail_chars
    if keep_chars <= 0 or len(text) <= keep_chars:
        return text

    omitted = len(text) - keep_chars
    tail = text[-tail_chars:] if tail_chars > 0 else ""
    return (
        text[:head_chars]
        + f"\n\n[SYSTEM LOG: {omitted} characters of this tool output have been pruned "
        f"(middle section) to save context window. If the pruned portion may contain "
        f"critical evidence, re-invoke the same command to obtain its full output.]\n\n"
        + tail
    )


def _compact_ai_gate_evidence(messages: list) -> list:
    """从请求副本的历史 AIMessage 中移除 gates[*].evidence。

    只移除 evidence，保留 gate 的 status/closure_basis。VMCoreAnalysisStep
    在解析历史消息时会用 gate 状态复核 is_conclusive，不能把 gates 整体删掉。
    """
    compacted: list = []
    stripped_gates = 0
    saved_chars = 0

    for msg in messages:
        if not isinstance(msg, AIMessage) or not isinstance(msg.content, str):
            compacted.append(msg)
            continue

        try:
            payload = json.loads(msg.content)
        except (TypeError, ValueError):
            compacted.append(msg)
            continue
        if not isinstance(payload, dict):
            compacted.append(msg)
            continue

        gates = payload.get("gates")
        if not isinstance(gates, dict):
            compacted.append(msg)
            continue

        changed = False
        for gate in gates.values():
            if not isinstance(gate, dict):
                continue
            evidence = gate.get("evidence")
            if not evidence:
                continue
            saved_chars += len(evidence) if isinstance(evidence, str) else len(str(evidence))
            gate["evidence"] = None
            stripped_gates += 1
            changed = True

        if changed:
            msg = msg.model_copy(
                update={
                    "content": json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    )
                }
            )
        compacted.append(msg)

    if stripped_gates:
        logger.info(
            f"[compress] stripped gate evidence from historical AI messages: "
            f"{stripped_gates} gates, saved ~{saved_chars} chars"
        )
    return compacted


def _trim_old_reasoning(messages: list, keep_count: int) -> list:
    """只保留最近 keep_count 条 AI 的完整 reasoning_content，旧消息摘要化。

    字段本身必须保留：DeepSeek-Reasoner 多轮请求要求 assistant 消息携带
    reasoning_content。这里用短摘要替代旧内容，而不是删除字段。
    """
    ai_indices = [i for i, msg in enumerate(messages) if isinstance(msg, AIMessage)]
    if keep_count > 0:
        old_indices = set(ai_indices[:-keep_count])
    else:
        old_indices = set(ai_indices)

    trimmed: list = []
    saved_chars = 0
    for index, msg in enumerate(messages):
        if index not in old_indices or not isinstance(msg, AIMessage):
            trimmed.append(msg)
            continue

        reasoning = msg.additional_kwargs.get("reasoning_content")
        marker = "\n[SYSTEM LOG: earlier reasoning_content was summarized to save context.]"
        if not isinstance(reasoning, str) or len(reasoning) <= (
            REASONING_DIGEST_CHARS + len(marker)
        ):
            trimmed.append(msg)
            continue

        digest = reasoning[:REASONING_DIGEST_CHARS] + marker
        additional_kwargs = dict(msg.additional_kwargs)
        additional_kwargs["reasoning_content"] = digest
        saved_chars += len(reasoning) - len(digest)
        trimmed.append(msg.model_copy(update={"additional_kwargs": additional_kwargs}))

    if saved_chars:
        logger.info(
            f"[compress] summarized old reasoning_content: saved ~{saved_chars} chars"
        )
    return trimmed


def _emergency_downgrade(
    messages: list,
    recent_tool_messages_to_keep: int,
    max_tool_output_chars: int,
) -> tuple[list, int, int]:
    """紧急降级：只压旧证据/DEDUP，保护最近工具和最近 DEDUP。"""
    tool_indices = [
        i for i, msg in enumerate(messages) if isinstance(msg, ToolMessage)
    ]
    recent_indices = _recent_index_set(tool_indices, recent_tool_messages_to_keep)
    dedup_positions = [
        i
        for i in tool_indices
        if isinstance(messages[i].content, str)
        and messages[i].content.startswith(DEDUP_PREFIX)
    ]
    recent_dedup_positions = (
        set(dedup_positions[-DEDUP_FULL_PRESERVE_COUNT:])
        if dedup_positions
        else set()
    )

    downgraded: list = []
    count = 0
    saved_chars = 0
    for index, msg in enumerate(messages):
        if (
            isinstance(msg, ToolMessage)
            and isinstance(msg.content, str)
            and index not in recent_indices
            and index not in recent_dedup_positions
        ):
            content = msg.content
            is_evidence = (
                msg.name in EVIDENCE_TOOL_COMMANDS
                or content.startswith(DEDUP_PREFIX)
            )
            if is_evidence and len(content) > max_tool_output_chars:
                head_chars = max_tool_output_chars * 3 // 5
                tail_chars = max_tool_output_chars - head_chars
                truncated = _truncate_middle(content, head_chars, tail_chars)
                saved_chars += len(content) - len(truncated)
                msg = msg.model_copy(update={"content": truncated})
                count += 1
        downgraded.append(msg)
    return downgraded, count, saved_chars


def estimate_prompt_chars(messages: list, system_message: Optional[SystemMessage] = None) -> int:
    """统计将进入请求的字符数（含 system 与历史 reasoning_content）。"""
    total_chars = 0
    if system_message is not None and isinstance(system_message.content, str):
        total_chars += len(system_message.content)

    for message in messages:
        content = getattr(message, "content", "")
        total_chars += len(content) if isinstance(content, str) else len(str(content))
        if isinstance(message, AIMessage):
            reasoning = message.additional_kwargs.get("reasoning_content")
            if isinstance(reasoning, str):
                total_chars += len(reasoning)
    return total_chars


def estimate_prompt_tokens(
    messages: list,
    system_message: Optional[SystemMessage] = None,
    chars_per_token: Optional[float] = None,
) -> int:
    """按字符数估算 prompt tokens，并计入消息固定开销。"""
    ratio = chars_per_token or _CHARS_PER_TOKEN
    chars = estimate_prompt_chars(messages, system_message)
    message_count = len(messages) + (1 if system_message is not None else 0)
    return math.ceil(chars / ratio) + 4 * message_count + 16


def calibrate_chars_per_token(prompt_chars: int, prompt_tokens: int) -> None:
    """用真实 usage.prompt_tokens 校准字符/token 比值。"""
    global _CHARS_PER_TOKEN
    if prompt_chars <= 0 or prompt_tokens <= 0:
        return
    observed = prompt_chars / prompt_tokens
    if not 1.0 <= observed <= 3.0:
        return
    _CHARS_PER_TOKEN = 0.7 * _CHARS_PER_TOKEN + 0.3 * observed


def plan_output_budget(
    prompt_tokens: int,
    prompt_chars: int,
    config: LLMBudgetConfig,
) -> OutputBudget:
    """在 thinking 预算和正文预算之间做选择，绝不静默回落到 4,096。"""
    available = (
        config.context_window - prompt_tokens - config.safety_margin_tokens
    )

    if config.thinking_enabled and available >= config.min_thinking_output_tokens:
        return OutputBudget(
            max_tokens=min(config.max_output_tokens, available),
            thinking=True,
            reasoning_effort=config.reasoning_effort,
            prompt_tokens=prompt_tokens,
            prompt_chars=prompt_chars,
        )

    if available >= config.min_output_tokens:
        return OutputBudget(
            max_tokens=min(config.max_text_output_tokens, available),
            thinking=False,
            reasoning_effort="low",
            prompt_tokens=prompt_tokens,
            prompt_chars=prompt_chars,
        )

    return OutputBudget(
        max_tokens=None,
        thinking=False,
        reasoning_effort="low",
        prompt_tokens=prompt_tokens,
        prompt_chars=prompt_chars,
        must_compress=True,
        reason=(
            f"prompt_tokens={prompt_tokens} leaves only {available} tokens "
            f"for output (minimum {config.min_output_tokens})"
        ),
    )


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
    *,
    system_message: Optional[SystemMessage] = None,
    prompt_budget_tokens: Optional[int] = None,
    reasoning_history_keep: int = 2,
    chars_per_token: Optional[float] = None,
) -> list:
    """
    在发送给 LLM 前对消息历史进行保守压缩，降低 token 消耗。

    策略：
    1. 历史 AIMessage 的 gates[*].evidence 一律清空，但保留 gate 状态，
       因为 VMCoreAnalysisStep 解析时会用 status/closure_basis 校验结论。
    2. 最近几条 ToolMessage 默认保留更多内容，但若单条过大仍会压缩到上限。
    3. 证据型命令（bt/log/kmem/struct/dis 等）的 ToolMessage 享受比普通消息更宽松的
       evidence_tool_limit_chars 上限，但这个"宽松待遇"来自一个全局预算
       EVIDENCE_BUDGET_CHARS，且预算按"离当前最近"优先分配（逆序预分配，见下方
       _assign_evidence_tiers）：越靠近当前轮次的证据消息越优先拿到宽松档，
       预算耗尽后，越久远的证据消息越先降级到 FALLBACK_EVIDENCE_LIMIT_CHARS。
    4. DEDUP 消息（LLM 主动重请求同一命令得到的完整缓存输出）中，最近
       DEDUP_FULL_PRESERVE_COUNT 条享受远高于普通证据消息的上限
       （DEDUP_HARD_CAP_CHARS），但并非无限。
    5. 如果给了 system_message + prompt_budget_tokens，压缩后按真实 token 估算复检；
       仍超限时先摘要旧 reasoning_content，再只对旧证据/DEDUP 做紧急降级，
       保护最近工具消息和最近 DEDUP。
    6. 此函数不修改 AgentState，仅返回压缩后的副本用于当次 LLM 调用。

    如果最终仍超预算，本函数只记录 warning；由 prepare_messages_for_llm 决定是否
    关闭 thinking 或抛出 ContextBudgetError，避免把超限请求发给 LLM。
    """
    # 先移除历史 AI 消息中重复的全量 gate evidence（通常是上下文膨胀的主因）。
    messages = _compact_ai_gate_evidence(messages)

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
            if len(msg.content) > DEDUP_HARD_CAP_CHARS:
                dedup_head_chars = DEDUP_HARD_CAP_CHARS * 3 // 5
                dedup_tail_chars = DEDUP_HARD_CAP_CHARS - dedup_head_chars
                truncated_content = _truncate_middle(
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
        tool_limit = max_tool_output_chars
        if index in recent_tool_indices:
            tool_limit = max(tool_limit, max_recent_tool_output_chars)
        if is_evidence:
            tool_limit = max(
                tool_limit, evidence_tiers.get(index, FALLBACK_EVIDENCE_LIMIT_CHARS)
            )
        # 如果当前消息内容长度超过了对应限制，则进行截断处理
        if len(msg.content) > tool_limit:
            tool_head_chars = tool_limit * 3 // 5
            tool_tail_chars = tool_limit - tool_head_chars
            truncated_content = _truncate_middle(
                msg.content,
                tool_head_chars,
                tool_tail_chars,
            )
            msg = msg.model_copy(update={"content": truncated_content})
            truncated_tool_count += 1
            if index in recent_tool_indices:
                truncated_recent_tool_count += 1
            if is_evidence:
                truncated_evidence_tool_count += 1
            tool_chars_after += len(msg.content)
            compressed.append(msg)
        else:
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

    # --- 总量安全检查：压缩后若仍超预算，先摘要旧 reasoning，再降级旧工具 ---
    if system_message is not None and prompt_budget_tokens is not None:
        total_tokens = estimate_prompt_tokens(
            compressed, system_message, chars_per_token
        )
        if total_tokens > prompt_budget_tokens:
            before_tokens = total_tokens
            compressed = _trim_old_reasoning(compressed, reasoning_history_keep)
            total_tokens = estimate_prompt_tokens(
                compressed, system_message, chars_per_token
            )
            logger.warning(
                f"[compress] prompt estimate {before_tokens} > budget "
                f"{prompt_budget_tokens}; after reasoning trim: {total_tokens}"
            )

        if total_tokens > prompt_budget_tokens:
            before_tokens = total_tokens
            compressed, downgraded, saved_chars = _emergency_downgrade(
                compressed,
                recent_tool_messages_to_keep,
                max_tool_output_chars,
            )
            total_tokens = estimate_prompt_tokens(
                compressed, system_message, chars_per_token
            )
            logger.warning(
                f"[compress] emergency downgrade of old evidence/DEDUP: "
                f"messages={downgraded}, saved={saved_chars} chars, "
                f"tokens {before_tokens} -> {total_tokens} "
                f"(budget {prompt_budget_tokens})"
            )

        if total_tokens > prompt_budget_tokens:
            logger.warning(
                f"[compress] still over prompt budget after all stages: "
                f"{total_tokens} > {prompt_budget_tokens}; caller must decide"
            )
        else:
            logger.info(
                f"[compress] within prompt budget: {total_tokens} <= {prompt_budget_tokens}"
            )

    return compressed


def prepare_messages_for_llm(
    system_message: SystemMessage,
    messages: list,
    config: LLMBudgetConfig,
) -> tuple[list, OutputBudget]:
    """压缩消息并规划输出预算；无法放入预算时抛 ContextBudgetError。"""
    target_output_tokens = (
        config.min_thinking_output_tokens
        if config.thinking_enabled
        else config.min_output_tokens
    )
    prompt_budget_tokens = (
        config.context_window - target_output_tokens - config.safety_margin_tokens
    )
    compressed = compress_messages_for_llm(
        messages,
        system_message=system_message,
        prompt_budget_tokens=prompt_budget_tokens,
        reasoning_history_keep=config.reasoning_history_keep,
        chars_per_token=config.chars_per_token,
    )
    prompt_chars = estimate_prompt_chars(compressed, system_message)
    prompt_tokens = estimate_prompt_tokens(
        compressed, system_message, config.chars_per_token
    )
    budget = plan_output_budget(prompt_tokens, prompt_chars, config)

    # thinking 预算放不下时，plan 会选择关闭 thinking；若提示词本身仍放不下，
    # 再按正文模式的最低输出预算压缩一次。
    if budget.must_compress and config.thinking_enabled:
        prompt_budget_tokens = (
            config.context_window - config.min_output_tokens - config.safety_margin_tokens
        )
        compressed = compress_messages_for_llm(
            compressed,
            system_message=system_message,
            prompt_budget_tokens=prompt_budget_tokens,
            reasoning_history_keep=config.reasoning_history_keep,
            chars_per_token=config.chars_per_token,
        )
        prompt_chars = estimate_prompt_chars(compressed, system_message)
        prompt_tokens = estimate_prompt_tokens(
            compressed, system_message, config.chars_per_token
        )
        budget = plan_output_budget(prompt_tokens, prompt_chars, config)

    if budget.must_compress:
        raise ContextBudgetError(
            f"Prompt does not fit context budget: {budget.reason}; "
            f"context_window={config.context_window}, "
            f"prompt_tokens={prompt_tokens}, prompt_chars={prompt_chars}"
        )

    logger.info(
        f"[budget] prompt_est={prompt_tokens} chars={prompt_chars} "
        f"window={config.context_window} max_tokens={budget.max_tokens} "
        f"thinking={budget.thinking} effort={budget.reasoning_effort}"
    )
    return compressed, budget


def _recent_index_set(indices: list[int], keep_count: int) -> set[int]:
    if keep_count <= 0:
        return set()
    # 获取最近的索引集合，保留最后 keep_count 个索引
    return set(indices[-keep_count:])