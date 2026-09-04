#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一 LLM 配置：从本目录 .env（或同名系统环境变量）读取各模型服务的
base_url / api_key / model_name，经 get_llm 工厂创建 ChatOpenAI 实例。

两类角色，模型完全由 .env 决定（无内置默认值）：
- 视觉解析（PDF 页 -> Markdown）：VISION_BASE_URL / VISION_MODEL / VISION_API_KEY
- 推理（抽取 / 问答，通用）      ：REASONING_BASE_URL / REASONING_MODEL / REASONING_API_KEY

Embedding（向量库）走本地 Ollama：EMBEDDING_BASE_URL / EMBEDDING_MODEL。

.env 配置优先级最高（load_dotenv 不覆盖已存在的同名环境变量，
即系统环境变量优先于 .env）。
"""

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_ollama import OllamaEmbeddings
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from logger import get_logger

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")  # 不覆盖系统里已存在的同名变量

log = get_logger()

# ---- 业务角色：.env 前缀即角色名，调用方无需指定模型 ----------------------
VISION_ROLE = "vision"
REASONING_ROLE = "reasoning"

# 角色显示名（仅用于日志与报错提示）
_ROLE_LABEL = {
    VISION_ROLE: "视觉解析（PDF页->Markdown）",
    REASONING_ROLE: "推理（抽取/问答）",
}


def _secret(value: str) -> SecretStr | None:
    """空字符串视为未配置，返回 None 以便 ChatOpenAI 回退读取环境变量"""
    return SecretStr(value) if value else None


def resolve_llm_config(role: str) -> dict[str, str | None]:
    """返回某角色生效的 {base_url, api_key, model_name}（全部来自 .env/环境变量）。

    role 为 "vision" 或 "reasoning"，对应读取 {ROLE_UPPER}_BASE_URL /
    {ROLE_UPPER}_API_KEY / {ROLE_UPPER}_MODEL。
    """
    if role not in _ROLE_LABEL:
        known = ", ".join(_ROLE_LABEL)
        log.error("[config] 不支持的 LLM 角色: %s（可用: %s）", role, known)
        raise ValueError(f"Unsupported LLM role: {role}（可用: {known}）")
    prefix = role.upper()
    base_url = os.getenv(f"{prefix}_BASE_URL") or ""
    api_key = os.getenv(f"{prefix}_API_KEY") or ""
    model_name = os.getenv(f"{prefix}_MODEL") or ""

    log.info("[config] %s(%s) 配置: base_url=%s, model_name=%s",
             role, _ROLE_LABEL[role], base_url, model_name)
    return {"base_url": base_url, "api_key": api_key, "model_name": model_name}


def get_llm(
    role: str,
    temperature: float = 0.1,
    *,
    max_tokens: int | None = None,
    timeout: float | None = None,
    max_retries: int = 2,
    enable_thinking: bool | None = None,
) -> ChatOpenAI:
    """统一的 LLM 实例工厂，配置完全来自 .env / 环境变量（见 resolve_llm_config）。

    role 传 "vision" / "reasoning"；可选 max_tokens / timeout / max_retries
    覆盖创建参数。enable_thinking=False 时经 extra_body 关闭 Qwen3 思考模式
    （结构化抽取等追求吞吐的场景适用），None 则沿用模型默认。三项配置
    （BASE_URL/MODEL/API_KEY）缺一即报错并列出缺哪几项。
    """
    resolved = resolve_llm_config(role)
    base_url = resolved["base_url"] or ""
    api_key = resolved["api_key"] or ""
    model_name = resolved["model_name"] or ""
    prefix = role.upper()
    label = _ROLE_LABEL[role]

    missing = [name for name, val in
               ((f"{prefix}_BASE_URL", base_url),
                (f"{prefix}_MODEL", model_name),
                (f"{prefix}_API_KEY", api_key)) if not val]
    if missing:
        msg = f"{label} 配置不完整：请在 sida-agent/.env 设置 {', '.join(missing)}。"
        log.error("[config] %s", msg)
        raise ValueError(msg)

    kwargs: dict[str, Any] = {
        "model": model_name,
        "api_key": _secret(api_key),
        "temperature": temperature,
        "max_retries": max_retries,
        "base_url": base_url,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if timeout is not None:
        kwargs["timeout"] = timeout
    if enable_thinking is not None:
        kwargs["extra_body"] = {"enable_thinking": enable_thinking}
    return ChatOpenAI(**kwargs)


def get_vision_llm(
    temperature: float = 0.1,
    *,
    max_tokens: int | None = None,
    timeout: float | None = None,
    max_retries: int = 2,
) -> ChatOpenAI:
    """视觉解析模型（PDF 页 -> Markdown），服务配置来自 .env 的 VISION_*。"""
    return get_llm(
        VISION_ROLE, temperature=temperature,
        max_tokens=max_tokens, timeout=timeout, max_retries=max_retries,
    )


def get_reasoning_llm(
    temperature: float = 0.1,
    *,
    max_tokens: int | None = None,
    timeout: float | None = None,
    max_retries: int = 2,
    enable_thinking: bool | None = None,
) -> ChatOpenAI:
    """推理模型（知识抽取 / 问答），服务配置来自 .env 的 REASONING_*。"""
    return get_llm(
        REASONING_ROLE, temperature=temperature,
        max_tokens=max_tokens, timeout=timeout, max_retries=max_retries,
        enable_thinking=enable_thinking,
    )


def get_embedding_model():
    """获取向量嵌入模型（本地 Ollama），配置来自 .env 的 EMBEDDING_*。

    - EMBEDDING_BASE_URL：Ollama 服务地址，如 http://localhost:11636
    - EMBEDDING_MODEL   ：嵌入模型名，如 nomic-embed-text:latest
    两项缺一即报错；向量库写入/检索均使用该模型做嵌入。
    """
    base_url = os.getenv("EMBEDDING_BASE_URL") or ""
    model_name = os.getenv("EMBEDDING_MODEL") or ""
    missing = [name for name, val in
               (("EMBEDDING_BASE_URL", base_url),
                ("EMBEDDING_MODEL", model_name)) if not val]
    if missing:
        msg = f"Embedding 配置不完整：请在 sida-agent/.env 设置 {', '.join(missing)}。"
        log.error("[config] %s", msg)
        raise ValueError(msg)

    log.info("[config] embedding 配置: base_url=%s, model=%s", base_url, model_name)
    # trust_env=False：本机 Ollama 直连，避免被 Windows 系统代理劫持（否则 502）
    return OllamaEmbeddings(model=model_name, base_url=base_url,
                            client_kwargs={"trust_env": False})