"""
改进版本：不依赖 with_structured_output，手动解析 JSON
"""

import os
import json
import re
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


# ... (保持所有 BaseModel 定义不变，这里省略以节省空间)
# 从原文件复制：DiagnosticStep, RootCauseMapping, DiagnosisDSL, CaseSummary, DiagnosticKnowledgeLibrary


class DiagnosticStep(BaseModel):
    step_number: int
    thought: str
    action: str
    observation: str


class RootCauseMapping(BaseModel):
    phenomenon: str
    possible_cause: str


class DiagnosisDSL(BaseModel):
    scenario: str
    symptoms: List[str]
    workflow: List[DiagnosticStep]
    root_cause_analysis: List[RootCauseMapping]
    data_patterns: Optional[List[str]] = None
    model_config = ConfigDict(populate_by_name=True)


class CaseSummary(BaseModel):
    case_id: str
    scenario: str
    unique_symptoms: List[str]
    confidence_score: float


class DiagnosticKnowledgeLibrary(BaseModel):
    collection_name: str = "vmcore_expert_methodologies"
    retrieved_cases: List[DiagnosisDSL]
    comparison_map: List[CaseSummary]
    common_initial_actions: List[str] = Field(default_factory=list)
    model_config = ConfigDict(arbitrary_types_allowed=True)


# Prompt 定义
diagnostic_knowledge_prompt_v2 = """You are a Linux kernel failure modeling expert.

# Task
Integrate {count} diagnostic DSL documents into a structured "Diagnostic Knowledge Library".

# Input Data
{retrieved_dsl_list_json}

# Output Requirement
Output ONLY a valid JSON object with this exact structure:
{{
  "collection_name": "vmcore_expert_methodologies",
  "retrieved_cases": [...],  // Include all input DSL objects as-is
  "comparison_map": [        // Create comparison summaries
    {{
      "case_id": "case_1",
      "scenario": "...",
      "unique_symptoms": ["...", "..."],
      "confidence_score": 1.0
    }}
  ],
  "common_initial_actions": ["bt -a", "..."]  // Common commands across cases
}}

CRITICAL RULES:
1. Output ONLY JSON, no markdown, no code blocks
2. Start with {{ and end with }}
3. Include ALL input cases in retrieved_cases
4. Create case_id as "case_1", "case_2", etc.
5. confidence_score is always 1.0 for this task
"""


def extract_json_from_text(text: str) -> dict:
    """从文本中提取 JSON，处理 markdown 代码块等"""
    # 移除 markdown 代码块
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)

    # 尝试找到第一个 { 和最后一个 }
    start_idx = text.find("{")
    end_idx = text.rfind("}")

    if start_idx == -1 or end_idx == -1:
        raise ValueError("No JSON object found in response")

    json_str = text[start_idx : end_idx + 1]
    return json.loads(json_str)


def main():
    llm = ChatOpenAI(
        api_key="sk-b5480f840a794c69a0af1732459f3ae4",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0,
    )

    # 读取已生成的 DSL 文件
    dsl_files = [
        "dsl/6348992.json",
        "dsl/7086442.json",
        "dsl/3870151.json",
    ]

    dsl_list = []
    for dsl_file in dsl_files:
        with open(dsl_file, "r", encoding="utf-8") as f:
            dsl_data = json.load(f)
            dsl_list.append(dsl_data)

    print(f"已加载 {len(dsl_list)} 个 DSL 文件")

    # 准备输入
    dsl_list_json = json.dumps(dsl_list, ensure_ascii=False, indent=2)
    print(f"输入数据大小：{len(dsl_list_json)} 字符")

    # 构造 prompt
    prompt_text = diagnostic_knowledge_prompt_v2.format(
        count=len(dsl_list), retrieved_dsl_list_json=dsl_list_json
    )

    print("\n调用 LLM 整合知识库...")
    response = llm.invoke(prompt_text)

    print(f"响应长度：{len(response.content)} 字符")
    print(f"响应预览 (前 500 字符):\n{response.content[:500]}\n")

    # 手动解析 JSON
    try:
        json_data = extract_json_from_text(response.content)
        print("\n成功提取 JSON 数据！")

        # 验证并创建 Pydantic 对象
        knowledge_library = DiagnosticKnowledgeLibrary(**json_data)

        print(f"\n整合成功！")
        print(f"- 集合名称：{knowledge_library.collection_name}")
        print(f"- 检索案例数：{len(knowledge_library.retrieved_cases)}")
        print(f"- 对比摘要数：{len(knowledge_library.comparison_map)}")
        print(f"- 通用初始操作：{len(knowledge_library.common_initial_actions)} 个")

        if knowledge_library.comparison_map:
            print("\n案例对比摘要：")
            for summary in knowledge_library.comparison_map:
                print(f"  - {summary.case_id}: {summary.scenario}")
                print(f"    关键症状：{', '.join(summary.unique_symptoms[:3])}")

        # 保存结果
        output_path = "dsl/integrated_knowledge_library_v2.json"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(knowledge_library.model_dump_json(indent=2))
        print(f"\n已保存到：{output_path}")

        return knowledge_library

    except json.JSONDecodeError as e:
        print(f"\nJSON 解析失败：{e}")
        print(f"响应内容:\n{response.content}")
        return None
    except Exception as e:
        print(f"\n创建对象失败：{e}")
        import traceback

        traceback.print_exc()
        return None


if __name__ == "__main__":
    os.environ["LANGSMITH_TRACING"] = "false"
    result = main()
    if result:
        print("\n✅ 整合完成！")
    else:
        print("\n❌ 整合失败")
