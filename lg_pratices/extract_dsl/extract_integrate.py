import os
import json
from langchain_openai import ChatOpenAI
from langchain.messages import SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from typing import TypedDict, List, Tuple, Annotated, Literal, Union, Optional
from langchain_core.documents import Document
from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel, Field, ConfigDict
import operator

extract_dsl_prompt = ChatPromptTemplate.from_template(
    """You are a senior Linux kernel diagnostics expert extracting diagnostic logic from documentation.

    # Task
    Extract ALL diagnostic commands, steps, and metadata from the markdown document into structured JSON.

    # Extraction Rules

    ## 1. Workflow Steps - Extract EVERY Command
    Scan the document and extract ALL crash commands in the order they appear:

    **For each command found:**
    - **step_number**: Sequential number (1, 2, 3...)
    - **thought**: Brief reason for this check (5-15 words, extract from surrounding text)
    - **action**: EXACT command as written (preserve all flags, pipes, awk, grep)
    - **observation**: Key indicator to find (5-12 words, from doc or output snippet)

    **Command patterns to find:**
    - `crash>` prefix: `crash> bt -a`
    - Inline mentions: "run `spinlock_t <addr>`"
    - Code blocks with commands
    - Piped commands: Keep the ENTIRE pipe chain intact

    **Examples:**
    - Document: "crash> foreach UN bt | awk '/{{#[1-4]/}} {{print $3,$5}}'"
      Extract: `{{"action": "foreach UN bt | awk '/{{#[1-4]/}} {{print $3,$5}}'"}}` (keep complete)

    - Document: "Check timer base: crash> struct tvec_base.timer_jiffies 0xffff8840691a8000"
      Extract: `{{"thought": "Check timer base lag", "action": "struct tvec_base.timer_jiffies 0xffff8840691a8000"}}`

    ## 2. Symptoms - Extract Trigger Keywords
    Find 3-7 specific symptoms from "Issue" or panic message section:
    - Stack trace symbols (e.g., "RIP: _spin_lock_irqsave")
    - Error messages (e.g., "Watchdog detected hard LOCKUP")
    - Panic strings

    ## 3. Root Cause Analysis - DIRECT EXTRACTION ONLY
    **DO NOT summarize or interpret.**

    Find section titled "Root Cause" or similar headings:
    - Extract each bullet point or paragraph AS-IS
    - Keep original wording verbatim
    - Create one entry per distinct cause/phenomenon mentioned

    # Quality Checks
    - Workflow must have AT LEAST as many steps as crash commands in document
    - Do NOT merge similar commands - keep all distinct instances
    - Preserve exact syntax including addresses, filters, pipe chains
    - Root cause entries must use original document wording

    # Output Format
    - Valid JSON only (no markdown code blocks)
    - English only
    - Use abbreviations where clear (e.g., "addr", "ptr", "CPU")

    # Output JSON Schema:
    {schema}

    # Input Content:
    {markdown_content}
"""
)


class DiagnosticStep(BaseModel):
    """
    Represents an atomic diagnostic step for a ReAct Agent.
    """

    step_number: int = Field(
        ..., description="The sequence number of the execution step."
    )
    thought: str = Field(
        ...,
        description="The expert's logical reasoning (in English) explaining why this action is performed.",
    )
    action: str = Field(
        ...,
        description="The specific command to execute in the crash utility, e.g., 'bt -a' or 'struct spinlock_t <address>'.",
    )
    observation: str = Field(
        ...,
        description="The expected abnormal indicators or key metrics to look for in the action output.",
    )


class DiagnosisDSL(BaseModel):
    """
    A structured diagnostic methodology model for vmcore analysis.
    Designed to transform unstructured documents into a machine-executable DSL for ReAct Agents.
    """

    scenario: str = Field(
        ...,
        description="The specific problem scenario this diagnostic flow applies to (e.g., 'Spinlock Deadlock').",
    )
    symptoms: List[str] = Field(
        ...,
        description="A list of key symptoms that trigger this diagnostic flow (e.g., specific panic keywords or symbols).",
    )

    workflow: List[DiagnosticStep] = Field(
        ...,
        description="The structured investigation workflow. The Agent will execute these steps sequentially.",
    )

    root_cause_analysis: List[str] = Field(
        ...,
        description="A list of root cause analysis statements extracted from the document.",
    )

    model_config = ConfigDict(populate_by_name=True)  # 替换 Config 类


# 3. 优化后的 Prompt (侧重于全面性和细节保留，
# 当前的 Prompt 过于强调“压缩” (Compression) 和“去重” (Deduplication)，
# 而不是“整合” (Integration) 和“覆盖” (Coverage)。)
diagnostic_dict_prompt = ChatPromptTemplate.from_template(
    """You are a Linux Kernel Diagnostic Architect. Build a COMPREHENSIVE decision matrix.

    # Task
    Integrate {count} diagnostic workflows into a unified, master "Condition -> Action" rule set.
    Your goal is to create a knowledge base that covers ALL distinct scenarios and edge cases found in the input data.

    # Input Data
    {retrieved_dsl_list_json}

    # Integration Guidelines
    1. **Maximize Coverage**: Do NOT over-simplify. Ensure that every unique diagnostic path and specific check from the inputs is represented in the matrix.
    2. **Preserve Nuance**: If two workflows look similar but check different fields or have different thresholds, create a BRANCH based on the specific symptom or trigger. Do not merge them if it loses technical accuracy.
    3. **Trigger Specificity**: The `trigger` must be specific enough to distinguish between different scenarios (e.g., distinguish "General Panic" from "Null Pointer Dereference").
    4. **Action Precision**: Keep the specific `crash` commands and argument hints accurate. Do not generalize commands to the point of uselessness.
    5. **Consolidation Strategy**: Merge ONLY truly identical steps. If there is a variation, keep both as separate branches.

    # Output Requirement
    - Output strictly according to the JSON Schema.
    - **Language**: English.
    - **Fix**: Ensure double curly braces are used for placeholders in the prompt examples, but single braces in the JSON output.

    # Output JSON Schema
    {schema}
"""
)


# 1. 简化后的 Branch 定义
class DiagnosticBranch(BaseModel):
    """
    Simplified diagnostic rule: Condition -> Action.
    """

    # 移除 branch_id，使用列表索引即可
    # 移除 match_type，默认为语义匹配
    # 触发此操作的先前步骤观察结果。请保持简洁。
    trigger: str = Field(
        ...,
        description="The observation from previous step that triggers this action. Keep it concise.",
    )
    # 带有占位符的命令模板，例如 'struct spinlock {addr}'。
    action: str = Field(
        ...,
        description="Command template with placeholders, e.g., 'struct spinlock {addr}'.",
    )
    # 简短语法定义如何填充占位符。例如，“addr：来自 RBX 寄存器”。
    # 合并 required_variables 和 variable_extraction_hint
    arg_hints: Optional[str] = Field(
        None,
        description="Short syntax defining how to fill placeholders. E.g., 'addr: from RBX register'.",
    )

    # 缩短 rationale
    # 此行动之简要缘由。
    why: str = Field(
        ...,
        description="Brief reason for this action.",
    )

    # 重命名 expected_outcome -> expect
    # 预期要寻找的输出结果。
    expect: str = Field(
        ...,
        description="Expected output to look for.",
    )

    # 简化终止逻辑
    is_end: bool = Field(
        False,
        description="True if this is a root cause conclusion.",
    )


# 2. 简化后的 Dict 定义
class DiagnosticDict(BaseModel):
    """
    Compact diagnostic knowledge base.
    """

    summary: str = Field(..., description="Brief scenario summary")

    # 缩短字段名
    init_cmds: List[str] = Field(
        ..., description="Common initial commands (e.g., 'bt -a', 'sys')."
    )

    matrix: List[DiagnosticBranch] = Field(..., description="The decision matrix.")

    # 移除 potential_root_causes，因为可以通过遍历 matrix 中 is_end=True 的节点获得


class LoggingCallbackHandler(BaseCallbackHandler):
    def on_llm_start(self, serialized, prompts, **kwargs):
        print(f"\n=== LLM 调用开始 ===")
        print(f"模型：{serialized.get('name', 'unknown')}")

    def on_llm_end(self, response, **kwargs):
        print(f"=== LLM 调用结束 ===")
        # 打印响应的详细信息
        if hasattr(response, "generations"):
            print(f"生成数：{len(response.generations)}")
            if response.generations and response.generations[0]:
                first_gen = response.generations[0][0]
                print(f"消息类型：{type(first_gen.message)}")
                if hasattr(first_gen.message, "content"):
                    content_preview = str(first_gen.message.content)[:200]
                    print(f"内容预览：{content_preview}...")
                if hasattr(first_gen.message, "additional_kwargs"):
                    print(f"额外参数：{first_gen.message.additional_kwargs.keys()}")
        print(f"响应生成完成\n")

    def on_chain_start(self, serialized, inputs, **kwargs):
        print(f"\n=== Chain 开始执行 ===")
        # 安全地处理不同类型的 inputs
        if isinstance(inputs, dict):
            print(f"输入键：{list(inputs.keys())}")
        else:
            print(f"输入类型：{type(inputs)}")

    def on_chain_end(self, outputs, **kwargs):
        print(f"=== Chain 执行结束 ===")
        print(f"输出类型：{type(outputs)}")
        # 如果输出不是预期的类型，打印详细信息
        if outputs is None:
            print("警告：输出为 None")
        elif isinstance(outputs, dict):
            print(f"输出键：{list(outputs.keys())}")
        print()


def main():
    # 创建 dsl 目录（如果不存在）
    os.makedirs("dsl", exist_ok=True)

    llm = ChatOpenAI(
        api_key="sk-b5480f840a794c69a0af1732459f3ae4",  # type: ignore
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0,  # temperature 的作用是控制生成文本的随机性，值越低，生成的文本越确定和一致
    )

    md_list = [
        "md/6348992.md",
        "md/3870151.md",
        "md/7041099.md",
        "md/5764681.md",
        "md/6988986.md",
        "md/3379041.md",
    ]

    dsl_list = []

    for md_path in md_list:
        # 判断 dsl 目录下是否已经存在对应的 json 文件，存在则跳过
        output_path = os.path.join(
            "dsl", os.path.basename(md_path).replace(".md", ".json")
        )
        if os.path.exists(output_path):
            print(f"跳过已存在的文件：{output_path}")
            with open(output_path, "r", encoding="utf-8") as f:
                dsl_data = f.read()
                dsl_list.append(dsl_data)
            continue

        # 打开 md 目录下下 7086442.md 文件，读取内容
        with open(md_path, "r", encoding="utf-8") as f:
            markdown_content = f.read()

        extract_dsl = extract_dsl_prompt | llm.with_structured_output(
            DiagnosisDSL, method="json_mode"  # 改用 json_mode
        )
        dsl_schema = DiagnosisDSL.model_json_schema()
        response = extract_dsl.invoke(
            {
                "markdown_content": markdown_content,
                "schema": json.dumps(dsl_schema, indent=2),
            }
        )

        # 存储为字典对象，方便后续处理
        if isinstance(response, DiagnosisDSL):
            # 如果是 DiagnosisDSL 实例，使用 model_dump()
            dsl_dict = response.model_dump()
        elif isinstance(response, dict):
            # 如果已经是字典，直接使用
            dsl_dict = response
        else:
            # 其他情况，抛出错误
            raise TypeError(f"Expected DiagnosisDSL or dict, got {type(response)}")

        dsl_list.append(dsl_dict)

        # 保存为 JSON 文件
        dsl_data = json.dumps(dsl_dict, ensure_ascii=False, indent=2)
        # 将 dsl_data 写入文件
        output_path = os.path.join(
            "dsl", os.path.basename(md_path).replace(".md", ".json")
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(dsl_data)

        print(f"Extracted DSL from {md_path}")

        # # 1. 提取用于检索的语义特征 (指纹)

    # 整合多个 DSL 成为 DiagnosticKnowledgeLibrary
    print(f"Extracted {len(dsl_list)} DSL documents.")

    dd_schema = DiagnosticDict.model_json_schema()

    diagnostic_dict = diagnostic_dict_prompt | llm.with_structured_output(
        DiagnosticDict, method="json_mode"  # 改用 json_mode
    )

    print("\n开始整合诊断知识库...")
    print(f"DSL 案例数量：{len(dsl_list)}")

    # 将字典列表转换为 JSON 字符串用于 prompt
    dsl_list_json = json.dumps(dsl_list, ensure_ascii=False, indent=2)

    # # 先测试不带结构化输出的 LLM 调用，看看原始响应
    # print("\n=== 测试：调用 LLM 获取原始响应 ===")
    # test_chain = diagnostic_knowledge_prompt | llm
    # test_response = test_chain.invoke(
    #     {
    #         "retrieved_dsl_list_json": dsl_list_json,
    #         "count": len(dsl_list),
    #         "schema": json.dumps(library_schema, indent=2),
    #     }
    # )
    # print(f"test_response:{test_response}")

    try:
        diagnostic_dict_response = diagnostic_dict.invoke(
            {
                "retrieved_dsl_list_json": dsl_list_json,
                "count": len(dsl_list),
                "schema": json.dumps(dd_schema, indent=2),
            },
            config={"callbacks": [LoggingCallbackHandler()]},
        )

        # 如果 diagnostic_dict_response 不为 None，json 格式输出
        if diagnostic_dict_response is not None and isinstance(
            diagnostic_dict_response, DiagnosticDict
        ):
            # 使用 model_dump_json() 直接转换为 JSON 字符串
            diagnostic_dict_json = diagnostic_dict_response.model_dump_json(indent=2)
            # 将 JSON 字符串写入 dsl 目录下的 diagnostic_knowledge_library.json 文件
            with open(
                os.path.join("dsl", "diagnostic_knowledge_library.json"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(diagnostic_dict_json)
        else:
            print(f"错误：整合知识库失败！")

    except Exception as e:
        print(f"\n错误：整合知识库失败！")
        print(f"异常类型：{type(e).__name__}")
        print(f"异常信息：{str(e)}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    # 先设置环境变量
    os.environ["TAVILY_API_KEY"] = "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"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGSMITH_API_KEY"] = "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" # fmt: skip

    main()
