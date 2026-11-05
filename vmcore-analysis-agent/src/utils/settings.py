#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime settings loaded from environment variables and the project .env file.

配置来源统一为环境变量；项目根目录的 .env 仅作为本地开发的默认值，
进程环境变量优先（便于容器/CI 注入）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# override=False：已存在的进程环境变量优先于 .env。
load_dotenv(_ENV_FILE, override=False)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _optional(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def _parse_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise RuntimeError(
        f"Invalid boolean value for {name}: {raw!r}; expected one of "
        f"{sorted(_TRUE_VALUES | _FALSE_VALUES)}."
    )


def _parse_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid float value for {name}: {raw!r}") from exc


def _parse_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer value for {name}: {raw!r}") from exc
    if value < minimum:
        raise RuntimeError(
            f"Invalid integer value for {name}: {value}; expected >= {minimum}."
        )
    return value


@dataclass(frozen=True)
class Settings:
    """Validated runtime settings."""

    reasoning_api_key: str
    reasoning_base_url: str
    reasoning_model: str
    reasoning_thinking: str
    reasoning_effort: str
    reasoning_temperature: float
    reasoning_context_window: int
    reasoning_max_output_tokens: int
    reasoning_min_thinking_output_tokens: int
    reasoning_history_keep: int
    langsmith_tracing: bool

    def masked_summary(self) -> dict[str, Any]:
        """Return a log-safe view of the settings (never includes the API key)."""

        return {
            "reasoning_base_url": self.reasoning_base_url,
            "reasoning_model": self.reasoning_model,
            "reasoning_thinking": self.reasoning_thinking,
            "reasoning_effort": self.reasoning_effort,
            "reasoning_temperature": self.reasoning_temperature,
            "reasoning_context_window": self.reasoning_context_window,
            "reasoning_max_output_tokens": self.reasoning_max_output_tokens,
            "reasoning_min_thinking_output_tokens": self.reasoning_min_thinking_output_tokens,
            "reasoning_history_keep": self.reasoning_history_keep,
            "langsmith_tracing": self.langsmith_tracing,
        }


@lru_cache(maxsize=1)
def get_reasoning_model_name(default: str = "unknown") -> str:
    """Return the configured model name without requiring the full settings."""

    return os.environ.get("REASONING_MODEL", "").strip() or default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate settings once per process."""

    thinking = _optional("REASONING_THINKING", "disabled").lower()
    if thinking not in {"enabled", "disabled"}:
        raise RuntimeError(
            f"Invalid value for REASONING_THINKING: {thinking!r}; "
            "expected 'enabled' or 'disabled'."
        )

    context_window = _parse_int("REASONING_CONTEXT_WINDOW", 524_288, 1)
    max_output_tokens = _parse_int("REASONING_MAX_OUTPUT_TOKENS", 48_000, 1)
    min_thinking_output_tokens = _parse_int(
        "REASONING_MIN_THINKING_OUTPUT_TOKENS", 40_960, 1
    )
    if max_output_tokens < min_thinking_output_tokens:
        raise RuntimeError(
            "REASONING_MAX_OUTPUT_TOKENS must be >= "
            "REASONING_MIN_THINKING_OUTPUT_TOKENS."
        )

    return Settings(
        # 模型名与 URL 保持原始大小写，避免 lower() 破坏大小写敏感的路径。
        reasoning_api_key=_require("REASONING_API_KEY"),
        reasoning_base_url=_require("REASONING_BASE_URL"),
        reasoning_model=_require("REASONING_MODEL"),
        reasoning_thinking=thinking,
        reasoning_effort=_optional("REASONING_EFFORT", "max").lower(),
        reasoning_temperature=_parse_float("REASONING_TEMPERATURE", 0.1),
        reasoning_context_window=context_window,
        reasoning_max_output_tokens=max_output_tokens,
        reasoning_min_thinking_output_tokens=min_thinking_output_tokens,
        reasoning_history_keep=_parse_int("REASONING_HISTORY_KEEP", 2, 0),
        langsmith_tracing=_parse_bool("LANGSMITH_TRACING", False),
    )