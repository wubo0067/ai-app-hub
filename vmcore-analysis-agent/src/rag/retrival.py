import os
import json
from pydantic import parse_raw_as
from .diagnostic_knowledge_base import DiagnosticKnowledgeBase


def _init_diagnostic_knowledge_base() -> DiagnosticKnowledgeBase:
    """初始化诊断知识库实例"""
    # 获取当前文件所在目录的绝对路径
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # 构建 JSON 文件的绝对路径
    diagnostic_knowledge_base_path = os.path.join(
        current_dir, "../../rag-preprocessing/data/ssl_diagnostic_knowledge.json"
    )
    diagnostic_knowledge_base_path = os.path.normpath(diagnostic_knowledge_base_path)

    # 同步读取 JSON 文件并解析为 DiagnosticKnowledgeBase 实例
    with open(diagnostic_knowledge_base_path, mode="r", encoding="utf-8") as f:
        content = f.read()
        diagnostic_knowledge_base = parse_raw_as(DiagnosticKnowledgeBase, content)
    return diagnostic_knowledge_base


# 全局变量，在模块被导入时自动初始化，且只执行一次
diagnostic_knowledge = _init_diagnostic_knowledge_base()
