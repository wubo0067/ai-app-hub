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

from .schema import VMCoreAnalysisStep

ModelT = TypeVar("ModelT", bound=BaseModel)


def _normalize_root_cause_class(content_str: str) -> str:
    """
    在 JSON 解析前对 root_cause_class 字段进行语义归一化。
    将常见的别名映射到合法的枚举值，避免因 schema 严格验证导致的解析失败。
    """
    # 定义常见别名到合法值的映射
    alias_mapping = {
        "pointer_corruption": "wild_pointer",  # pointer_corruption 更接近 wild_pointer 的语义
        "corruption": "memory_corruption",
        "memory_error": "memory_corruption",
        "address_corruption": "wild_pointer",
        "invalid_pointer": "wild_pointer",
    }

    # 使用正则表达式匹配并替换 root_cause_class 字段值
    for alias, canonical in alias_mapping.items():
        # 匹配 "root_cause_class": "pointer_corruption" 这样的模式
        pattern = r'("root_cause_class"\s*:\s*")' + re.escape(alias) + r'"'
        replacement = r"\1" + canonical + r'"'
        content_str = re.sub(pattern, replacement, content_str)

        # 也处理 null 值的情况（虽然不太可能）
        pattern_null = r'("root_cause_class"\s*:\s*)null'
        replacement_null = r'\1"' + canonical + r'"'
        # 只在特定上下文中替换 null，避免误替换

    return content_str


def select_analysis_content(
    content: Any, reasoning: str | None
) -> tuple[str | None, str | None]:
    """选择用于结构化解析的内容源，必要时回退到 reasoning_content。"""
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
    """尝试从原始 LLM 文本中修复并恢复指定的结构化模型。"""
    repaired_prefix = f"{log_prefix}: " if log_prefix else ""

    # 在任何修复尝试之前，先进行语义归一化
    content_str = _normalize_root_cause_class(content_str)

    try:
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
    """从结构化分析结果中构造 LangChain tool_calls。"""
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
        tool_args = {"command": " ".join(analysis_result.action.arguments)}

    return [
        {
            "name": tool_name,
            "args": tool_args,
            "id": f"call_{analysis_result.step_id}",
        }
    ]


def _extract_outer_json_object(content_str: str) -> str:
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
    pattern = r'("command_name"\s*:\s*"[^"]*"\s*,)\s*(\[)'
    return re.sub(pattern, r'\1 "arguments": \2', content_str)
