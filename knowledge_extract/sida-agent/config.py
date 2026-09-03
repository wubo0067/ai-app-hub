#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一 LLM 配置：从本目录 .env（或同名系统环境变量）读取各模型服务的
base_url / api_key / model_name，经 get_llm 工厂创建 ChatOpenAI 实例。

内置两类角色（均可被 .env 覆盖，见 sida-agent/.env）：
- 视觉解析（PDF 页 -> Markdown）  ：provider="vision"
- 推理（抽取 / 问答）            ：provider="deepseek"（或 qwen/openai）

.env 配置优先级最高（load_dotenv 不覆盖已存在的同名环境变量，
即系统环境变量优先于 .env）。
"""

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr

from logger import get_logger

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")  # 不覆盖系统里已存在的同名变量

log = get_logger()

# ---- 内置 provider 默认值 -------------------------------------------------
# base_url / model_name 可被环境变量 {PREFIX}_BASE_URL / {PREFIX}_MODEL 覆盖；
# api_key 优先读 {PREFIX}_API_KEY，为空时回退 key_env。
_PROVIDERS: dict[str, dict[str, str | None]] = {
    "vision": {
        "label": "视觉解析（PDF页->Markdown）",
        "base_url": "https://developer.amd.com.cn/radeon/api/v1",
        "model": "Qwen3.8-Flash-Next",
        "key_env": "DASHSCOPE_API_KEY",
    },
    "deepseek": {
        "label": "推理（抽取/问答）",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "label": "通义（阿里云百炼）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "vision_model": "qwen-vl-max",
        "key_env": "DASHSCOPE_API_KEY",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": None,  # None 表示官方默认端点
        "model": "gpt-4o-mini",
        "vision_model": "gpt-4o",
        "key_env": "OPENAI_API_KEY",
    },
}


def _secret(value: str) -> SecretStr | None:
    """空字符串视为未配置，返回 None 以便 ChatOpenAI 回退读取环境变量"""
    return SecretStr(value) if value else None


def _provider_config(provider: str) -> dict[str, str | None]:
    cfg = _PROVIDERS.get(provider)
    if cfg is None:
        known = ", ".join(_PROVIDERS)
        log.error("[config] 不支持的 LLM provider: %s（可用: %s）", provider, known)
        raise ValueError(f"Unsupported LLM provider: {provider}（可用: {known}）")
    return cfg


def resolve_llm_config(provider: str) -> dict[str, str | None]:
    """返回某 provider 生效的 {base_url, api_key, model_name}（.env/环境变量优先）。"""
    cfg = _provider_config(provider)
    prefix = provider.upper()
    base_url = os.getenv(f"{prefix}_BASE_URL") or cfg["base_url"]
    api_key = os.getenv(f"{prefix}_API_KEY") or os.getenv(cfg["key_env"] or "") or ""
    model_name = os.getenv(f"{prefix}_MODEL") or cfg["model"]
    return {"base_url": base_url, "api_key": api_key, "model_name": model_name}


def get_llm(
    provider: str = "deepseek",
    is_vision: bool = False,
    temperature: float = 0.1,
    *,
    max_tokens: int | None = None,
    timeout: float | None = None,
    max_retries: int = 2,
) -> ChatOpenAI:
    """统一的 LLM 实例工厂，配置来自 .env / 环境变量（见 resolve_llm_config）。

    视觉/推理双模型：provider 传内置角色名；视觉模型额外 is_vision=True。
    可选 max_tokens / timeout / max_retries 覆盖创建参数。
    """
    cfg = _provider_config(provider)
    resolved = resolve_llm_config(provider)
    base_url = resolved["base_url"]
    api_key = resolved["api_key"] or ""

    model_name = resolved["model_name"]
    if is_vision and cfg.get("vision_model"):
        model_name = cfg["vision_model"]

    if not api_key:
        log.error(
            "[config] %s(%s) 未配置 API Key：请在 sida-agent/.env 设置 %s_API_KEY "
            "（或系统环境变量 %s）。",
            provider, cfg.get("label"), provider.upper(), cfg.get("key_env"),
        )
        raise ValueError(
            f"{provider}({cfg.get('label')}) 未配置 API Key：请在 sida-agent/.env "
            f"设置 {provider.upper()}_API_KEY（或系统环境变量 {cfg.get('key_env')}）。"
        )

    kwargs: dict[str, Any] = {
        "model": model_name,
        "api_key": _secret(api_key),
        "temperature": temperature,
        "max_retries": max_retries,
    }
    if base_url:
        kwargs["base_url"] = base_url
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if timeout is not None:
        kwargs["timeout"] = timeout
    return ChatOpenAI(**kwargs)


def get_embedding_model():
    """获取向量嵌入模型（OpenAI 兼容；api_key/base_url 来自 .env）"""
    api_key = os.getenv("OPENAI_API_KEY", "") or ""
    base_url = os.getenv("OPENAI_BASE_URL") or None
    kwargs: dict[str, Any] = {
        "model": "text-embedding-3-small",
        "api_key": _secret(api_key or "mock-key"),
    }
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAIEmbeddings(**kwargs)