import os
import json
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import Optional, List

# 使用改进后的Prompt
detailed_prompt = ChatPromptTemplate.from_template(
    """Role: Expert Linux Kernel Diagnostic Architect specializing in Hard/Soft LOCKUP analysis.
    Task: Extract ALL diagnostic steps from DSL case into a DETAILED knowledge base.

    # CRITICAL REQUIREMENTS
    1. Extract EVERY diagnostic command, flag, pipe, and argument from workflow array
    2. Keep exact crash commands, memory addresses, and kernel structures
    3. Create specific triggers with placeholders
    4. Include detailed arg_hints with source information

    # FIELD SPECIFICATIONS
    - trigger: Specific symptom or observation
    - action: EXACT crash command with all flags and pipes
    - arg_hints: Detailed source for each placeholder
    - why: Detailed technical reason
    - expect: Specific pattern to look for

    # INPUT
    - DSL File: {dsl_content}

    # OUTPUT
    - Strictly follow JSON Schema.
    - Use single braces in JSON output (e.g., {{addr}}).
    - Schema: {schema}
    """
)


class DiagnosticBranch(BaseModel):
    trigger: str = Field(..., description="Specific symptom or observation")
    action: str = Field(..., description="Exact crash command with all flags and pipes")
    arg_hints: Optional[str] = Field(
        None, description="Detailed source information for each placeholder"
    )
    why: str = Field(..., description="Detailed technical reason")
    expect: str = Field(..., description="Specific pattern to look for")
    is_end: bool = Field(False, description="True only for root cause conclusions")


class DiagnosticDict(BaseModel):
    summary: str = "Detailed diagnostic matrix for Linux kernel lockup scenarios"
    init_cmds: List[str] = Field(..., description="Initial diagnostic commands")
    matrix: List[DiagnosticBranch] = Field(
        ..., description="Detailed diagnostic matrix"
    )


def test_single_file():
    """测试单个文件的详细提取"""
    file_path = "dsl/3379041.json"  # 使用新增的大文件

    print(f"测试文件: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            dsl_content = f.read()
            data = json.loads(dsl_content)

        print(f"文件大小: {len(dsl_content)} 字节")
        print(f"诊断步骤数: {len(data.get('workflow', []))}")
        print(f"场景: {data.get('scenario', '未知')}")

        # 检查前几个步骤
        print("\n前3个诊断步骤:")
        for i, step in enumerate(data.get("workflow", [])[:3], 1):
            print(f"  步骤 {step.get('step_number', i)}:")
            print(f"    思考: {step.get('thought', '')}")
            print(f"    动作: {step.get('action', '')}")
            print(f"    预期: {step.get('observation', '')}")
            print()

        # 初始化LLM
        llm = ChatOpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            temperature=0,
        )

        schema = DiagnosticDict.model_json_schema()
        chain = detailed_prompt | llm.with_structured_output(
            DiagnosticDict, method="json_mode"
        )

        print("调用LLM进行详细提取...")
        result = chain.invoke(
            {"dsl_content": dsl_content, "schema": json.dumps(schema, indent=2)}
        )

        print(f"\n提取结果:")
        print(f"总结: {result.summary}")
        print(f"初始命令数: {len(result.init_cmds)}")
        print(f"诊断分支数: {len(result.matrix)}")

        # 显示前几个分支
        print("\n前5个诊断分支:")
        for i, branch in enumerate(result.matrix[:5], 1):
            print(f"  分支 {i}:")
            print(f"    触发: {branch.trigger}")
            print(f"    动作: {branch.action}")
            print(f"    参数提示: {branch.arg_hints}")
            print(f"    原因: {branch.why}")
            print(f"    预期: {branch.expect}")
            print(f"    是否结束: {branch.is_end}")
            print()

        return result

    except Exception as e:
        print(f"错误: {e}")
        import traceback

        traceback.print_exc()
        return None


if __name__ == "__main__":
    os.environ["TAVILY_API_KEY"] = "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"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGSMITH_API_KEY"] = (
        "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"
    )

    test_single_file()
