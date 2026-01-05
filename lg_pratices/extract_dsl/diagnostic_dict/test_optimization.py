#!/usr/bin/env python3
"""
测试优化后的代码是否能正常工作
"""
import sys
import os

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dsl_integration import (
    extract_workflow_data,
    chunk_workflows,
    create_workflow_summary,
    DiagnosticKnowledge
)

def test_extract_workflow_data():
    """测试工作流数据提取"""
    print("测试工作流数据提取...")

    # 创建一个简单的测试 DSL 内容
    test_dsl = {
        "scenario": "Test scenario",
        "symptoms": ["symptom1", "symptom2"],
