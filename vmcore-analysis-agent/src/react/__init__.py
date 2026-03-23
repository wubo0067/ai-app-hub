#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# __init__.py - VMCore 分析 Agent React 模块包初始化
# Author: CalmWU
# Created: 2026-01-09

from .graph import create_agent_graph
from .graph_state import AgentState
from .llm_node import call_llm_analysis, structure_reasoning_content
from .logging_callback import GraphLoggingCallback, graph_logging_callback
from .report_generator import generate_markdown_report
from .schema import (
    CrashSignatureClass,
    FinalDiagnosis,
    GateEntry,
    Hypothesis,
    PartialDumpStatus,
    RootCauseClass,
    SuspectCode,
    ToolCall,
    VMCoreAnalysisStep,
)

__all__ = [
    "create_agent_graph",
    "AgentState",
    "call_llm_analysis",
    "structure_reasoning_content",
    "GraphLoggingCallback",
    "graph_logging_callback",
    "generate_markdown_report",
    "ToolCall",
    "SuspectCode",
    "FinalDiagnosis",
    "CrashSignatureClass",
    "RootCauseClass",
    "PartialDumpStatus",
    "Hypothesis",
    "GateEntry",
    "VMCoreAnalysisStep",
]
