#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from .layer0_system import LAYER0_SYSTEM_PROMPT_TEMPLATE
from .playbooks import PLAYBOOKS
from .sop_fragments import SOP_FRAGMENTS

__all__ = [
    # 第 0 层系统提示模板，这是基础的系统提示词模板，包含了 vmcore 分析的基本规则和指导方针。
    "LAYER0_SYSTEM_PROMPT_TEMPLATE",
    # 剧本集合，根据不同的崩溃类型提供专门的分析指南。这些剧本是针对特定崩溃类型的专门分析方法。
    "PLAYBOOKS",
    # 标准操作程序片段，根据分析过程中遇到的具体情况动态注入的高级分析指南。
    "SOP_FRAGMENTS",
]
