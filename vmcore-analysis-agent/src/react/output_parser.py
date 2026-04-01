#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# output_parser.py - LLM 输出解析和修复模块
# Author: CalmWU
# Created: 2026-03-23

import json
import re
from typing import Any, TypeVar

from json_repair import repair_json
from pydantic import BaseModel

from src.utils.logging import logger

from .schema import VMCoreAnalysisStep, VMCoreLLMAnalysisStep

ModelT = TypeVar("ModelT", bound=BaseModel)

_DISASM_LINE_RE = re.compile(
    r"^\s*0x(?P<addr>[0-9a-fA-F]+)\s+<[^>]+>:\s+(?P<inst>.+)$",
    re.MULTILINE,
)

# 正则表达式用于提取 RIP（指令指针）地址
_RIP_RE = re.compile(r"\bRIP:\s*(?:[0-9a-fA-F]+:)?(?P<addr>[0-9a-fA-F]{8,16})\b")

# 正则表达式用于提取 Oops 错误代码
_OOPS_RE = re.compile(r"\bOops:\s*(?P<code>[0-9a-fA-F]{4})\b")

# 非故障指令助记符集合 - 这些指令通常不会导致页面错误
_NON_FAULTING_MNEMONICS = {
    "pause",
    "nop",
    "nopl",
    "nopw",
    "sti",
    "cli",
    "ret",
    "hlt",
    "lfence",
    "sfence",
    "mfence",
}

# 写入指令助记符集合 - 这些指令执行写操作
_WRITE_MNEMONICS = {
    "push",
    "call",
    "stos",
    "stosb",
    "stosw",
    "stosl",
    "stosq",
}

# 读写指令助记符集合 - 这些指令同时执行读和写操作
_READWRITE_MNEMONICS = {
    "xchg",
    "cmpxchg",
    "cmpxchg8b",
    "cmpxchg16b",
    "xadd",
    "inc",
    "dec",
    "not",
    "neg",
    "or",
    "and",
    "xor",
    "add",
    "sub",
    "adc",
    "sbb",
}


def render_action_arguments(arguments: list[str]) -> str:
    """将结构化 action 参数渲染为可执行的 crash 命令字符串。

    LLM 输出的 arguments 是 JSON 字符串数组，不携带 shell 引号语义。
    对于 grep 模式中包含 alternation 的场景，需要在拼回命令时恢复引号，
    否则 `a|b|c` 会和真正的管道符混淆。

    Args:
        arguments: LLM 生成的命令参数列表

    Returns:
        str: 渲染后的可执行 crash 命令字符串

    使用场景：
        当 LLM 决定调用 crash 工具时，需要将参数列表转换为实际可执行的命令行字符串

    注意事项：
        特别处理 grep 命令中的管道符，避免与 shell 管道符混淆
    """
    rendered_tokens: list[str] = []
    in_grep_command = False
    grep_expects_pattern = False

    for token in arguments:
        if token == "|":
            rendered_tokens.append(token)
            in_grep_command = False
            grep_expects_pattern = False
            continue

        if token == "grep":
            rendered_tokens.append(token)
            in_grep_command = True
            grep_expects_pattern = True
            continue

        if in_grep_command and grep_expects_pattern and token.startswith("-"):
            rendered_tokens.append(token)
            grep_expects_pattern = True
            continue

        if in_grep_command and grep_expects_pattern and _is_quoted_shell_token(token):
            rendered_tokens.append(token)
            grep_expects_pattern = False
            continue

        if in_grep_command and grep_expects_pattern and "|" in token:
            rendered_tokens.append(f'"{token}"')
            grep_expects_pattern = False
            continue

        rendered_tokens.append(token)
        if in_grep_command and grep_expects_pattern:
            grep_expects_pattern = False

    return " ".join(rendered_tokens)


def _is_quoted_shell_token(token: str) -> bool:
    """检查 token 是否已经被 shell 引号包围。

    Args:
        token: 待检查的字符串 token

    Returns:
        bool: 如果 token 被双引号或单引号包围则返回 True
    """
    return len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}


def _normalize_root_cause_class(content_str: str) -> str:
    """
    在 JSON 解析前对 root_cause_class / corruption_mechanism 做语义归一化。

    兼容两类常见错位：
    1. root_cause_class 使用了旧别名；
    2. 模型把细粒度 corruption_mechanism 错塞进 root_cause_class。

    Args:
        content_str: 包含 root_cause_class 字段的 JSON 字符串

    Returns:
        str: 归一化后的 JSON 字符串

    用途：
        解决 LLM 输出中分类标签不一致的问题，确保后续验证通过

    注意事项：
        维护映射表的完整性，避免遗漏新的别名变体
    """
    # root_cause_class 的别名映射表
    root_cause_alias_mapping = {
        "pointer_corruption": "wild_pointer",
        "corruption": "memory_corruption",
        "memory_error": "memory_corruption",
        "address_corruption": "wild_pointer",
        "invalid_pointer": "wild_pointer",
    }

    # corruption_mechanism 到 root_cause 的映射表
    mechanism_to_root_cause_mapping = {
        "field_type_misuse": "dma_corruption",
        "missing_conversion": "dma_corruption",
        "write_corruption": "memory_corruption",
        "reinit_path_bug": "race_condition",
        "race_condition": "race_condition",
        "unknown": "unknown",
    }

    mechanism_pattern = "|".join(
        re.escape(value) for value in mechanism_to_root_cause_mapping
    )

    def _replace_root_cause(match: re.Match[str]) -> str:
        """替换 root_cause_class 值的回调函数"""
        prefix = match.group("prefix")
        value = match.group("value")

        # 如果值实际上是 corruption_mechanism，则同时设置两个字段
        if value in mechanism_to_root_cause_mapping:
            suffix = f',\n  "corruption_mechanism": "{value}"'
            if re.search(r'"corruption_mechanism"\s*:', content_str):
                suffix = ""
            return f'{prefix}{mechanism_to_root_cause_mapping[value]}"{suffix}'

        # 否则使用别名映射
        canonical = root_cause_alias_mapping.get(value, value)
        return f'{prefix}{canonical}"'

    # 应用 root_cause_class 的归一化
    root_cause_pattern = re.compile(
        r'(?P<prefix>"root_cause_class"\s*:\s*")(?P<value>[^"]+)"'
    )
    content_str = root_cause_pattern.sub(_replace_root_cause, content_str)

    # corruption_mechanism 的别名映射
    corruption_mechanism_alias_mapping = {
        "type_misuse": "field_type_misuse",
        "dma_type_misuse": "field_type_misuse",
        "overwrite": "write_corruption",
        "write_overwrite": "write_corruption",
        "reinit_bug": "reinit_path_bug",
    }
    for alias, canonical in corruption_mechanism_alias_mapping.items():
        pattern = r'("corruption_mechanism"\s*:\s*")' + re.escape(alias) + r'"'
        replacement = r"\1" + canonical + r'"'
        content_str = re.sub(pattern, replacement, content_str)

    # 警告检查：确保归一化后没有机制标签残留在 root_cause_class 中
    if re.search(rf'"root_cause_class"\s*:\s*"(?:{mechanism_pattern})"', content_str):
        logger.warning(
            "root_cause_class still contains a mechanism label after normalization; check mapping coverage."
        )

    return content_str


def select_analysis_content(
    content: Any, reasoning: str | None
) -> tuple[str | None, str | None]:
    """选择用于结构化解析的内容源，必要时回退到 reasoning_content。

    Args:
        content: LLM 的主要输出内容
        reasoning: LLM 的推理内容（备用）

    Returns:
        tuple: (用于结构化解析的内容，备用推理内容)

    使用场景：
        当主 content 为空或无效时，尝试从 reasoning 中提取有效信息

    注意事项：
        优先使用 content，只有在 content 无效时才回退到 reasoning
    """
    content_str = content if isinstance(content, str) else json.dumps(content)

    if content_str and content_str.strip():
        return content_str, None

    if reasoning and "{" in reasoning:
        logger.warning(
            "Content is empty/whitespace, attempting to extract JSON from reasoning_content"
        )
        return reasoning, None

    if reasoning and len(reasoning) > 50:
        return None, reasoning

    return content_str, None


def repair_analysis_step(
    content_str: str,
    *,
    log_prefix: str = "",
) -> VMCoreAnalysisStep | None:
    """修复并验证 VMCoreAnalysisStep 结构化输出。

    Args:
        content_str: 原始 LLM 输出字符串
        log_prefix: 日志前缀，用于调试

    Returns:
        VMCoreAnalysisStep | None: 修复后的分析步骤对象，失败时返回 None

    用途：
        将可能格式错误的 LLM 输出转换为有效的 VMCoreAnalysisStep 对象
    """
    return repair_structured_output(
        content_str,
        model_class=VMCoreAnalysisStep,
        log_prefix=log_prefix,
    )


def repair_structured_output(
    content_str: str,
    *,
    model_class: type[ModelT],
    log_prefix: str = "",
) -> ModelT | None:
    """尝试从原始 LLM 文本中修复并恢复指定的结构化模型。

    Args:
        content_str: 原始 LLM 输出字符串
        model_class: 目标 Pydantic 模型类
        log_prefix: 日志前缀

    Returns:
        BaseModel | None: 修复后的模型实例，失败时返回 None

    修复策略：
        1. 首先进行语义归一化
        2. 尝试使用 json_repair 库自动修复
        3. 如果失败，手动提取 JSON 对象并修复常见错误
        4. 最终验证并返回模型实例

    注意事项：
        所有修复步骤都会记录日志，便于调试 LLM 输出问题
    """
    repaired_prefix = f"{log_prefix}: " if log_prefix else ""

    # 在任何修复尝试之前，先进行语义归一化
    content_str = _normalize_root_cause_class(content_str)

    try:
        # 尝试使用 json_repair 库自动修复
        repaired_obj = repair_json(content_str, return_objects=True)
        if isinstance(repaired_obj, list):
            for item in repaired_obj:
                if isinstance(item, dict):
                    repaired_obj = item
                    break

        if isinstance(repaired_obj, dict):
            logger.warning(
                "%sSuccessfully repaired malformed JSON from LLM using json_repair.",
                repaired_prefix,
            )
            return model_class.model_validate(repaired_obj)
    except Exception as exc:
        logger.debug(
            "%sjson_repair failed: '%s', falling back to manual fix",
            repaired_prefix,
            exc,
        )

    try:
        # 手动修复策略
        fixed_content = _extract_outer_json_object(content_str)
        fixed_content = _normalize_invalid_escapes(fixed_content)
        fixed_content = _inject_missing_arguments_field(fixed_content)
        # 再次应用语义归一化，确保手动修复后的内容也被处理
        fixed_content = _normalize_root_cause_class(fixed_content)
        result = model_class.model_validate_json(fixed_content)
        logger.warning(
            "%sSuccessfully repaired malformed JSON from LLM (manual fix). Original: %s... Fixed: %s...",
            repaired_prefix,
            content_str[:200],
            fixed_content[:200],
        )
        return result
    except Exception as exc:
        logger.warning("%sJSON repair failed: '%s'", repaired_prefix, exc)
        return None


def build_tool_calls(
    analysis_result,
    *,
    is_last_step: bool,
    log_prefix: str = "",
) -> list[dict[str, Any]]:
    """从结构化分析结果中构造 LangChain tool_calls。

    Args:
        analysis_result: 分析结果对象
        is_last_step: 是否为最后一步
        log_prefix: 日志前缀

    Returns:
        list: 工具调用字典列表

    用途：
        将 LLM 的分析决策转换为实际的工具调用指令

    注意事项：
        如果是最后一步但仍有 action，则强制清除以确保结束分析
    """
    prefix = f"{log_prefix}: " if log_prefix else ""

    if is_last_step and analysis_result.action:
        logger.warning(
            "%sis_last_step=True but LLM still returned action. Stripping tool_calls to force conclusion.",
            prefix,
        )
        analysis_result.action = None

    if not analysis_result.action:
        logger.info("%sLLM did not call any tools, returning result directly.", prefix)
        return []

    tool_name = analysis_result.action.command_name
    logger.info("%sLLM decided to call tool: %s", prefix, tool_name)

    if tool_name == "run_script":
        tool_args = {"script": "\n".join(analysis_result.action.arguments)}
    else:
        tool_args = {
            "command": render_action_arguments(analysis_result.action.arguments)
        }

    return [
        {
            "name": tool_name,
            "args": tool_args,
            "id": f"call_{analysis_result.step_id}",
        }
    ]


def apply_executor_consistency_audit(
    analysis_step: VMCoreLLMAnalysisStep,
    state: dict[str, Any],
    *,
    log_prefix: str = "",
) -> VMCoreLLMAnalysisStep:
    """在 structured output 落地前执行额外的一致性审计。

    Args:
        analysis_step: LLM 分析步骤
        state: 当前状态
        log_prefix: 日志前缀

    Returns:
        VMCoreLLMAnalysisStep: 审计修正后的分析步骤

    审计内容：
        1. 基于故障上下文标准化 signature_class
        2. 基于故障上下文标准化 final_diagnosis
        3. 检测页面错误访问类型不匹配

    用途：
        确保 LLM 输出与实际 vmcore 上下文保持一致，防止幻觉
    """
    prefix = f"{log_prefix}: " if log_prefix else ""
    analysis_step = _normalize_signature_class_from_fault_context(
        analysis_step,
        state,
        log_prefix=log_prefix,
    )
    analysis_step = _normalize_final_diagnosis_for_fault_context(
        analysis_step,
        state,
        log_prefix=log_prefix,
    )
    mismatch = _detect_page_fault_access_mismatch(state)
    if mismatch is None:
        return analysis_step

    if _mentions_access_type_mismatch(analysis_step, mismatch):
        logger.debug(
            "%sExecutor audit found an access-type mismatch, but the model already referenced it.",
            prefix,
        )
        return analysis_step

    audit_note = (
        "Executor audit: unresolved x86 page-fault access-type contradiction. "
        f"{mismatch['summary']}"
    )
    logger.warning("%s%s", prefix, audit_note)

    if audit_note not in analysis_step.reasoning:
        analysis_step.reasoning = f"{audit_note} {analysis_step.reasoning}".strip()

    if analysis_step.additional_notes:
        if audit_note not in analysis_step.additional_notes:
            analysis_step.additional_notes = (
                f"{analysis_step.additional_notes} {audit_note}"
            ).strip()
    else:
        analysis_step.additional_notes = audit_note

    if analysis_step.root_cause_class not in {None, "unknown"}:
        analysis_step.root_cause_class = "unknown"

    if analysis_step.is_conclusive:
        analysis_step.is_conclusive = False
        analysis_step.final_diagnosis = None
        analysis_step.fix_suggestion = None

    if analysis_step.confidence not in {None, "low"}:
        analysis_step.confidence = "low"

    return analysis_step


def _normalize_signature_class_from_fault_context(
    analysis_step: VMCoreLLMAnalysisStep,
    state: dict[str, Any],
    *,
    log_prefix: str = "",
) -> VMCoreLLMAnalysisStep:
    """基于故障上下文标准化 signature_class。

    Args:
        analysis_step: 分析步骤
        state: 当前状态
        log_prefix: 日志前缀

    Returns:
        VMCoreLLMAnalysisStep: 修正后的分析步骤

    逻辑：
        如果 signature_class 是 general_protection_fault 但上下文显示是页面错误，
        则修正为 pointer_corruption
    """
    prefix = f"{log_prefix}: " if log_prefix else ""
    if analysis_step.signature_class != "general_protection_fault":
        return analysis_step

    text = _collect_state_text(state)
    if not _is_kernel_paging_request_page_fault_context(text):
        return analysis_step

    analysis_step.signature_class = "pointer_corruption"
    audit_note = (
        "Executor audit: Oops 0x0000 with BUG: unable to handle kernel paging request "
        "is a page-fault context, so signature_class was corrected from "
        "general_protection_fault to pointer_corruption."
    )
    logger.warning("%s%s", prefix, audit_note)

    if audit_note not in analysis_step.reasoning:
        analysis_step.reasoning = f"{audit_note} {analysis_step.reasoning}".strip()

    if analysis_step.additional_notes:
        if audit_note not in analysis_step.additional_notes:
            analysis_step.additional_notes = (
                f"{analysis_step.additional_notes} {audit_note}"
            ).strip()
    else:
        analysis_step.additional_notes = audit_note

    return analysis_step


def _normalize_final_diagnosis_for_fault_context(
    analysis_step: VMCoreLLMAnalysisStep,
    state: dict[str, Any],
    *,
    log_prefix: str = "",
) -> VMCoreLLMAnalysisStep:
    """基于故障上下文标准化 final_diagnosis 中的文本。

    Args:
        analysis_step: 分析步骤
        state: 当前状态
        log_prefix: 日志前缀

    Returns:
        VMCoreLLMAnalysisStep: 修正后的分析步骤

    用途：
        将诊断文本中的"general protection fault"替换为"page fault"，
        确保术语与实际故障上下文一致
    """
    prefix = f"{log_prefix}: " if log_prefix else ""
    diagnosis = analysis_step.final_diagnosis
    if diagnosis is None:
        return analysis_step

    text = _collect_state_text(state)
    if not _is_kernel_paging_request_page_fault_context(text):
        return analysis_step

    changed_fields: list[str] = []
    if _contains_general_protection_fault_text(diagnosis.crash_type):
        diagnosis.crash_type = _replace_general_protection_fault_text(
            diagnosis.crash_type,
            replacement="kernel paging request",
        )
        changed_fields.append("final_diagnosis.crash_type")

    if _contains_general_protection_fault_text(diagnosis.root_cause):
        diagnosis.root_cause = _replace_general_protection_fault_text(
            diagnosis.root_cause,
            replacement="page fault",
        )
        changed_fields.append("final_diagnosis.root_cause")

    if _contains_general_protection_fault_text(diagnosis.detailed_analysis):
        diagnosis.detailed_analysis = _replace_general_protection_fault_text(
            diagnosis.detailed_analysis,
            replacement="page fault",
        )
        changed_fields.append("final_diagnosis.detailed_analysis")

    if not changed_fields:
        return analysis_step

    audit_note = (
        "Executor audit: page-fault context wording corrected in "
        + ", ".join(changed_fields)
        + "; general protection fault phrasing was normalized to page-fault wording."
    )
    logger.warning("%s%s", prefix, audit_note)

    if audit_note not in analysis_step.reasoning:
        analysis_step.reasoning = f"{audit_note} {analysis_step.reasoning}".strip()

    if analysis_step.additional_notes:
        if audit_note not in analysis_step.additional_notes:
            analysis_step.additional_notes = (
                f"{analysis_step.additional_notes} {audit_note}"
            ).strip()
    else:
        analysis_step.additional_notes = audit_note

    return analysis_step


def _is_kernel_paging_request_page_fault_context(text: str) -> bool:
    """判断文本是否包含内核页面请求页面错误上下文。

    Args:
        text: 待检查的文本

    Returns:
        bool: 如果是页面错误上下文则返回 True

    判断条件：
        1. 包含"BUG: unable to handle kernel paging request"
        2. Oops 错误代码为 0x0000（表示页面错误）
    """
    if not text:
        return False

    lowered = text.lower()
    if "bug: unable to handle kernel paging request" not in lowered:
        return False

    oops_match = _OOPS_RE.search(text)
    return oops_match is not None and oops_match.group("code").lower() == "0000"


def _contains_general_protection_fault_text(text: str) -> bool:
    """检查文本是否包含通用保护错误相关文本。

    Args:
        text: 待检查的文本

    Returns:
        bool: 如果包含则返回 True
    """
    return bool(re.search(r"\bgeneral protection fault\b", text, flags=re.IGNORECASE))


def _replace_general_protection_fault_text(text: str, *, replacement: str) -> str:
    """将文本中的通用保护错误替换为指定文本。

    Args:
        text: 原始文本
        replacement: 替换文本

    Returns:
        str: 替换后的文本
    """
    return re.sub(
        r"\bgeneral protection fault\b",
        replacement,
        text,
        flags=re.IGNORECASE,
    )


def _extract_outer_json_object(content_str: str) -> str:
    """从可能包含额外文本的字符串中提取最外层的 JSON 对象。

    Args:
        content_str: 包含 JSON 的字符串

    Returns:
        str: 提取的 JSON 字符串

    算法：
        使用括号计数法找到完整的 JSON 对象边界
    """
    first_brace = content_str.find("{")
    if first_brace == -1:
        return content_str

    brace_count = 0
    last_brace = -1
    for index in range(first_brace, len(content_str)):
        if content_str[index] == "{":
            brace_count += 1
        elif content_str[index] == "}":
            brace_count -= 1
            if brace_count == 0:
                last_brace = index
                break

    if last_brace == -1:
        return content_str

    logger.info("Extracted JSON from position %s to %s", first_brace, last_brace + 1)
    return content_str[first_brace : last_brace + 1]


def _normalize_invalid_escapes(content_str: str) -> str:
    """修复 JSON 中的无效转义字符。

    Args:
        content_str: 原始字符串

    Returns:
        str: 修复后的字符串

    修复的转义：
        \| -> |
        \/ -> /
        \> -> >
        \< -> <
        \& -> &
    """
    invalid_escapes = [
        (r"\|", "|"),
        (r"\/", "/"),
        (r"\>", ">"),
        (r"\<", "<"),
        (r"\&", "&"),
    ]
    fixed_content = content_str
    for pattern, replacement in invalid_escapes:
        fixed_content = fixed_content.replace(pattern, replacement)
    return fixed_content


def _inject_missing_arguments_field(content_str: str) -> str:
    """为缺少 arguments 字段的 JSON 注入该字段。

    Args:
        content_str: JSON 字符串

    Returns:
        str: 注入 arguments 字段后的 JSON 字符串

    用途：
        修复 LLM 输出中 command_name 后直接跟数组而缺少 arguments 键的问题
    """
    pattern = r'("command_name"\s*:\s*"[^"]*"\s*,)\s*(\[)'
    return re.sub(pattern, r'\1 "arguments": \2', content_str)


def _detect_page_fault_access_mismatch(
    state: dict[str, Any],
) -> dict[str, str] | None:
    """检测页面错误访问类型不匹配。

    Args:
        state: 当前状态

    Returns:
        dict | None: 如果发现不匹配则返回详细信息，否则返回 None

    检测逻辑：
        1. 从 Oops 代码解码预期的访问方向（读/写/执行）
        2. 分析 RIP 指令的实际访问类型
        3. 比较两者是否一致

    用途：
        发现 LLM 分析中可能忽略的关键矛盾点
    """
    text = _collect_state_text(state)
    if not text:
        return None

    oops_match = _OOPS_RE.search(text)
    rip_match = _RIP_RE.search(text)
    if oops_match is None or rip_match is None:
        return None

    error_code = int(oops_match.group("code"), 16)
    access_direction = _decode_access_direction(error_code)
    if access_direction == "unknown":
        return None

    instructions = [
        (int(match.group("addr"), 16), match.group("inst").strip())
        for match in _DISASM_LINE_RE.finditer(text)
    ]
    if not instructions:
        return None

    rip_addr = int(rip_match.group("addr"), 16)
    rip_index = next(
        (index for index, (addr, _) in enumerate(instructions) if addr == rip_addr),
        None,
    )
    if rip_index is None:
        return None

    rip_instruction = instructions[rip_index][1]
    candidate_instruction = rip_instruction
    candidate_access = _classify_instruction_access(rip_instruction)

    # 如果 RIP 指令无法确定访问类型，向前查找最近的可确定指令
    if candidate_access in {"none", "unknown"}:
        for previous_index in range(rip_index - 1, -1, -1):
            previous_instruction = instructions[previous_index][1]
            previous_access = _classify_instruction_access(previous_instruction)
            if previous_access not in {"none", "unknown"}:
                candidate_instruction = previous_instruction
                candidate_access = previous_access
                break

    if candidate_access == "unknown":
        return None

    mismatch = False
    if access_direction == "write" and candidate_access not in {"write", "readwrite"}:
        mismatch = True
    elif access_direction == "read" and candidate_access not in {"read", "readwrite"}:
        mismatch = True
    elif access_direction == "execute" and candidate_access != "execute":
        mismatch = True

    if not mismatch:
        return None

    return {
        "summary": (
            f"Oops 0x{oops_match.group('code')} decodes to {access_direction} fault, but "
            f"the candidate instruction `{candidate_instruction}` is classified as {candidate_access}; "
            f"RIP instruction `{rip_instruction}` must not be accepted as a complete explanation until this is reconciled."
        ),
        "expected": access_direction,
        "candidate": candidate_access,
        "instruction": candidate_instruction,
        "rip_instruction": rip_instruction,
    }


def _collect_state_text(state: dict[str, Any]) -> str:
    """从状态中收集所有文本内容。

    Args:
        state: 当前状态字典

    Returns:
        str: 合并的文本内容

    用途：
        为审计函数提供完整的上下文信息
    """
    parts: list[str] = []
    for message in state.get("messages", []):
        content = getattr(message, "content", "")
        if isinstance(content, str):
            parts.append(content)
        elif content:
            try:
                parts.append(json.dumps(content, ensure_ascii=False))
            except TypeError:
                parts.append(str(content))
    return "\n".join(parts)


def _decode_access_direction(error_code: int) -> str:
    """根据 x86 页面错误错误代码解码访问方向。

    Args:
        error_code: 页面错误错误代码

    Returns:
        str: 访问方向（"execute", "write", "read", "unknown"）

    x86 页面错误错误代码位含义：
        bit 0: 0=不存在的页，1=保护违例
        bit 1: 0=读访问，1=写访问
        bit 2: 0=内核模式，1=用户模式
        bit 3: 0=非保留，1=保留位违例
        bit 4: 0=数据/非指令获取，1=指令获取
    """
    if error_code & 0x10:
        return "execute"
    if error_code & 0x2:
        return "write"
    return "read"


def _classify_instruction_access(instruction: str) -> str:
    """分类 x86 指令的内存访问类型。

    Args:
        instruction: x86 汇编指令

    Returns:
        str: 访问类型（"read", "write", "readwrite", "none", "unknown"）

    分类逻辑：
        1. 处理指令前缀（lock, rep 等）
        2. 根据助记符和操作数确定访问类型
        3. 特殊处理 mov、cmp 等指令
    """
    cleaned = instruction.strip().lower()
    for prefix in ("lock ", "rep ", "repz ", "repnz "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]

    if not cleaned:
        return "unknown"

    mnemonic, _, operand_text = cleaned.partition(" ")
    if mnemonic in _NON_FAULTING_MNEMONICS:
        return "none"
    if mnemonic in _WRITE_MNEMONICS:
        return "write"
    if mnemonic in _READWRITE_MNEMONICS and _has_memory_operand(operand_text):
        return "readwrite"
    if mnemonic == "lea":
        return "none"
    if mnemonic.startswith("j"):
        return "none"
    if mnemonic == "mov":
        operands = [operand.strip() for operand in operand_text.split(",", maxsplit=1)]
        if len(operands) == 2:
            src, dst = operands
            if _is_memory_operand(src) and not _is_memory_operand(dst):
                return "read"
            if not _is_memory_operand(src) and _is_memory_operand(dst):
                return "write"
            if _is_memory_operand(src) and _is_memory_operand(dst):
                return "readwrite"
    if mnemonic in {"cmp", "test", "bt", "bts", "btr", "btc"} and _has_memory_operand(
        operand_text
    ):
        return "read"
    if _has_memory_operand(operand_text):
        return "read"
    return "none"


def _has_memory_operand(operand_text: str) -> bool:
    """检查操作数文本是否包含内存操作数。

    Args:
        operand_text: 操作数文本

    Returns:
        bool: 如果包含内存操作数则返回 True

    内存操作数特征：
        1. 包含括号（如 (%rax)）
        2. 以%gs:或%fs:开头（段寄存器）
    """
    return any(_is_memory_operand(operand) for operand in operand_text.split(","))


def _is_memory_operand(operand: str) -> bool:
    """检查单个操作数是否为内存操作数。

    Args:
        operand: 单个操作数

    Returns:
        bool: 如果是内存操作数则返回 True
    """
    token = operand.strip().lower()
    return "(" in token or token.startswith("%gs:") or token.startswith("%fs:")


def _mentions_access_type_mismatch(
    analysis_step: VMCoreLLMAnalysisStep,
    mismatch: dict[str, str],
) -> bool:
    """检查分析步骤是否已经提到了访问类型不匹配。

    Args:
        analysis_step: 分析步骤
        mismatch: 不匹配信息

    Returns:
        bool: 如果已提及则返回 True

    用途：
        避免重复添加相同的审计注释
    """
    text_parts = [analysis_step.reasoning]
    if analysis_step.additional_notes:
        text_parts.append(analysis_step.additional_notes)
    if analysis_step.final_diagnosis is not None:
        text_parts.append(analysis_step.final_diagnosis.root_cause)
        text_parts.append(analysis_step.final_diagnosis.detailed_analysis)
        text_parts.extend(analysis_step.final_diagnosis.evidence)

    lowered = " ".join(text_parts).lower()
    keywords = [
        "error code",
        "write fault",
        "read fault",
        "access-type",
        "write-vs-read",
        "read-vs-write",
        "w/r",
        mismatch["instruction"].lower(),
    ]
    return (
        mismatch["expected"] in lowered
        and "contrad" in lowered
        and any(keyword in lowered for keyword in keywords)
    )
