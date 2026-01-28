import os
import json
from .diagnostic_knowledge_base import DiagnosticKnowledgeBase


def _init_diagnostic_knowledge_base() -> DiagnosticKnowledgeBase:
    """初始化诊断知识库实例"""
    # 获取当前文件所在目录的绝对路径
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # 构建 JSON 文件的绝对路径
    diagnostic_knowledge_base_path = os.path.join(
        current_dir, "../../rag-preprocessing/data/rcu_stall_dkb.json"
    )
    diagnostic_knowledge_base_path = os.path.normpath(diagnostic_knowledge_base_path)

    # 添加调试日志
    if not os.path.exists(diagnostic_knowledge_base_path):
        raise FileNotFoundError(
            f"Knowledge base file not found, please check the path.:{diagnostic_knowledge_base_path}"
        )

    # 同步读取 JSON 文件并解析为 DiagnosticKnowledgeBase 实例
    with open(diagnostic_knowledge_base_path, mode="r", encoding="utf-8") as f:
        content = f.read()
        diagnostic_knowledge_base = DiagnosticKnowledgeBase.model_validate_json(content)
    return diagnostic_knowledge_base


# 全局变量，在模块被导入时自动初始化，且只执行一次
dkb = _init_diagnostic_knowledge_base()
