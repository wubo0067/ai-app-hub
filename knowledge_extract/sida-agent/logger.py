#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日志模块：控制台 + 文件双通道输出。

- 控制台：默认 INFO 及以上，纯消息格式（与 print 观感一致，便于交互运行）。
- 文件：output/sida_agent.log，记录 DEBUG 及以上（含时间戳/级别/模块名），
  完整保留流水线细节，便于事后调试与问题定位。
- 每次进程启动会在日志文件里写入一条 "新运行" 分隔行，区分不同次的运行。

用法：
    from logger import get_logger
    log = get_logger()    # 模块级获取（单例，重复调用安全）
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "output"   # 日志目录（项目根下 output/）
LOG_FILE = LOG_DIR / "sida_agent.log"                  # 日志文件路径

_CONSOLE_FMT = "%(message)s"
_FILE_FMT = "%(asctime)s | %(levelname)-7s | %(name)s.%(module)s | %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def parse_level(name: str) -> int:
    """把级别名（大小写不敏感，debug/info/warning/error）转成 logging 常量。"""
    level = _LEVELS.get(name.strip().lower())
    if level is None:
        raise SystemExit(f"错误：无效日志级别 '{name}'，可选：{'/'.join(_LEVELS)}")
    return level


def get_logger(level: int = logging.INFO) -> logging.Logger:
    """获取应用 logger（单例）。首次调用完成配置，之后调用可调整控制台级别。

    文件 handler 恒为 DEBUG（记录全部细节）；控制台 handler 级别由 level 决定。
    """
    log = logging.getLogger("sida_agent")
    if not log.handlers:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log.setLevel(logging.DEBUG)
        log.propagate = False

        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FILE_FMT, _DATE_FMT))

        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(logging.Formatter(_CONSOLE_FMT))

        log.addHandler(file_handler)
        log.addHandler(console_handler)
        log.info("---- 新运行开始（%s）| 日志文件：%s ----",
                 datetime.now().strftime(_DATE_FMT), LOG_FILE)
    else:
        # 已配置过：仅按参数调整控制台输出级别，文件始终保留 DEBUG 细节
        for handler in log.handlers:
            if not isinstance(handler, logging.FileHandler):
                handler.setLevel(level)
    return log
