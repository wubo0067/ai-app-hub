#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Author: calm.wu wubo0067@hotmail.com
Date: 2026-09-03 11:01:07
LastEditors: calm.wu wubo0067@hotmail.com
LastEditTime: 2026-09-14 20:26:44
FilePath: sida-agent/agent/state.py
Description: 定义 Agent 工作流状态的类型。

Copyright (c) 2026 by calm.wu, All Rights Reserved.
"""

from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class _AgentStateOptional(TypedDict, total=False):
    """Agent 工作流中由各节点分步填充的可选字段"""
    target_subject: Optional[str]    # 判定出的学科：physics / chemistry / math
    target_concept: Optional[str]    # 提取的核心锚点实体（知识点）
    intent: Optional[str]            # 提问意图：concept / find_problem / offtopic
    search_text: Optional[str]       # find_problem 时提炼出的题目内容特征文本
    graph_context: Dict[str, Any]    # 图谱检索出的教研上下文（概念拆解/公式/实验/题型/例题）
    vector_chunks: List[str]        # 向量库回表拿出的原题全文
    problem_chunks: List[str]       # find_problem 时向量语义检索命中的讲义页切片（含出处头）
    final_answer: str               # 最终生成的系统讲解
    history_summary: str            # chat 会话中被截断的旧对话压缩摘要（覆盖式，不随轮累积）

class CircuitAgentState(_AgentStateOptional):
    """Agent 工作流状态：query 为入口必填，其余字段由节点逐步填充。

    messages：chat 会话对话主通道（HumanMessage/AIMessage 自动累积，由
    checkpointer 按 thread_id 持久化）；manage_context 节点用 RemoveMessage
    截断超预算的旧消息并把被丢弃部分增量压缩进 history_summary。
    单轮模式（ask/all）不传 messages，节点正常回退到 query 字段。
    """
    query: str                      # 用户提问
    messages: Annotated[list[AnyMessage], add_messages]  # 对话历史