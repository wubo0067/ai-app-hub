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
    """You are a senior Linux kernel diagnostics expert...

    # Task
    Analyze the provided Markdown document to extract diagnostic logic, specific 'crash' tool commands, ,
    and data patterns. Your goal is to generate a structured JSON that a ReAct Agent can use to perform step-by-step ,
    troubleshooting.

    # STRICT LANGUAGE RULE
    - The entire JSON response MUST be in English.
    - Translate all Chinese analysis from the source document into professional technical English.

    # Content Guidelines
    1. **Thought**: Explain the technical reasoning. Why check this specific data? What kernel subsystem is involved?
    2. **Action**: Must be a valid 'crash' utility command (e.g., 'bt', 'struct', 'rd', 'irq').
    3. **Observation**: Describe the specific abnormal indicators (e.g., 'owner field is NULL', 'value exceeds 0xFFFFF').
    4. **Symptoms**: List specific keywords or stack trace symbols that identify this issue.

    # Output Format Requirement
    - Output MUST be a valid JSON string.
    - DO NOT include markdown code blocks (e.g., no ```json).
    - Language: Please provide the content in **Chinese** (especially for 'thought' and 'scenario'), 、
        but keep technical terms and commands in English.

    # JSON Schema:
    {{
    "type": "object",
    "properties": {{
        "scenario": {{ "type": "string", "description": "Problem classification" }},
        "symptoms": {{ "type": "array", "items": {{ "type": "string" }}, "description": "Key patterns like 'panic', 'divide error', or specific function names" }},
        "workflow": {{
        "type": "array",
        "items": {{
            "type": "object",
            "properties": {{
            "step_number": {{ "type": "integer" }},
            "thought": {{ "type": "string", "description": "Logic/Reasoning in Chinese" }},
            "action": {{ "type": "string", "description": "The exact crash command to run" }},
            "observation": {{ "type": "string", "description": "What abnormal result confirms the theory" }}
            }},
            "required": ["step_number", "thought", "action", "observation"]
        }}
        }},
        "root_cause_analysis": {{
        "type": "array",
        "items": {{
            "type": "object",
            "properties": {{
            "phenomenon": {{ "type": "string" }},
            "possible_cause": {{ "type": "string" }}
            }}
        }}
        }}
    }},
    "required": ["scenario", "symptoms", "workflow", "root_cause_analysis"]
    }}

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
        description="The expert's logical reasoning (in Chinese as requested) explaining why this action is performed.",
    )
    action: str = Field(
        ...,
        description="The specific command to execute in the crash utility, e.g., 'bt -a' or 'struct spinlock_t <address>'.",
    )
    observation: str = Field(
        ...,
        description="The expected abnormal indicators or key metrics to look for in the action output.",
    )


class RootCauseMapping(BaseModel):
    """
    The mapping between observed phenomena and their potential root causes.
    """

    phenomenon: str = Field(
        ...,
        description="A key abnormal phenomenon observed during the diagnostic process.",
    )
    possible_cause: str = Field(
        ...,
        description="The underlying logic or potential root cause corresponding to the phenomenon.",
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

    root_cause_analysis: List[RootCauseMapping] = Field(
        ...,
        description="The mapping table between extracted phenomena and their causes from the document.",
    )

    data_patterns: Optional[List[str]] = Field(
        None,
        description="Specific numerical patterns mentioned in the document, such as flag bits, offsets, or timeout thresholds.",
    )

    model_config = ConfigDict(populate_by_name=True)  # 替换 Config 类


diagnostic_knowledge_prompt = ChatPromptTemplate.from_template(
    """You are a Linux kernel failure modeling expert,
    skilled in integrating multiple historical cases into a knowledge base for decision-making.

    # Task
    You have received {count} diagnostic DSL documents retrieved from the expert knowledge base.
    Please integrate them into a structured "Diagnostic Knowledge Library" for subsequent diagnostic agent calls.

    # Input Data (Retrieved DSLs)
    The following are the retrieved {count} original DSL contents:
    {retrieved_dsl_list_json}

    # Output Requirement
    You MUST output ONLY a valid JSON object that strictly follows the schema below.
    Do NOT include any markdown formatting, code blocks, or explanatory text.
    Start your response directly with the JSON object.

    # Output JSON Schema
    {schema}

    Special Notes:
    1. **CaseSummary**: Extract the function names or flags that best represent the characteristics of each
    DSL (e.g., Case A focuses on `pi_lock`, Case B focuses on `rq->lock`).
    2. **Comparison Map**: Summarize the similarities and differences between these cases.
    3. **Common Actions**: If these examples all recommend executing certain
    commands first (such as `bt -a` or `timer -r`), please list them as common initial actions.

    # CRITICAL: Output ONLY valid JSON. No markdown, no explanations. Start with {{ and end with }}.
    # Language: Output must be in English.
"""
)


class CaseSummary(BaseModel):
    """Fingerprint for quick comparison of cases"""

    case_id: str
    scenario: str
    unique_symptoms: List[str] = Field(
        description="Unique symptoms or function symbols for this case"
    )
    confidence_score: float = Field(description="Similarity score during retrieval")


class DiagnosticKnowledgeLibrary(BaseModel):
    """
    Aggregated RAG knowledge base object.
    The Agent will hold this object as its "expert brain" during diagnosis.
    """

    collection_name: str = "vmcore_expert_methodologies"
    # Contains all retrieved original DSL objects
    retrieved_cases: List[DiagnosisDSL] = Field(
        description="Multiple retrieved expert DSL instances"
    )

    # Core: comparison map to help Agent decide "which one to look at"
    comparison_map: List[CaseSummary] = Field(
        description="Comparison summary of all retrieved cases, listing core features of each case for easy pattern matching by Agent"
    )

    # Recommended common initial actions (if all cases suggest checking bt -a first, summarize here)
    common_initial_actions: List[str] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)  # Replace Config class


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
        max_tokens=8192,  # 增加输出长度限制
    )

    md_list = [
        "md/6348992.md",
        "md/7086442.md",
        "md/3870151.md",
        # "md/7019939.md",
        # "md/7041099.md",
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
            DiagnosisDSL, method="function_calling"
        )

        response = extract_dsl.invoke({"markdown_content": markdown_content})

        # 存储为字典对象，方便后续处理
        dsl_dict = response.model_dump()
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
        # search_content = f"""
        # Scenario: {response.scenario}
        # Symptoms: {', '.join(response.symptoms)}
        # Root Causes: {'; '.join([rc.phenomenon for rc in response.root_cause_analysis])}
        # """

        # # 2. 将原始 JSON 存入元数据，方便 Agent 直接解析成 BaseModel
        # metadata = {
        #     "source": os.path.basename(md_path),  # 提取文件文件名，不包括目录名
        #     "scenario": response.scenario,
        #     "raw_dsl": dsl_data,  # 关键：存入完整 JSON
        # }

        # # 3. 创建 LangChain Document
        # doc = Document(page_content=search_content, metadata=metadata)

        # # 将 Document 对象转换为字典
        # doc_dict = {"search_content": doc.page_content, "metadata": doc.metadata}

        # with open(output_path, "w", encoding="utf-8") as f:
        #     json.dump(doc_dict, f, ensure_ascii=False, indent=2)

    # 整合多个 DSL 成为 DiagnosticKnowledgeLibrary
    print(f"Extracted {len(dsl_list)} DSL documents.")

    library_schema = DiagnosticKnowledgeLibrary.model_json_schema()

    diagnostic_knowledge = diagnostic_knowledge_prompt | llm.with_structured_output(
        DiagnosticKnowledgeLibrary, method="json_mode"  # 改用 json_mode
    )

    print("\n开始整合诊断知识库...")
    print(f"DSL 案例数量：{len(dsl_list)}")

    # 将字典列表转换为 JSON 字符串用于 prompt
    dsl_list_json = json.dumps(dsl_list, ensure_ascii=False, indent=2)
    print(f"输入数据大小：{len(dsl_list_json)} 字符")

    # 先测试不带结构化输出的 LLM 调用，看看原始响应
    print("\n=== 测试：调用 LLM 获取原始响应 ===")
    test_chain = diagnostic_knowledge_prompt | llm
    test_response = test_chain.invoke(
        {
            "retrieved_dsl_list_json": dsl_list_json,
            "count": len(dsl_list),
            "schema": json.dumps(library_schema, indent=2),
        }
    )
    print(f"原始响应类型：{type(test_response)}")
    if hasattr(test_response, "content"):
        content_str = str(test_response.content)
        print(f"响应长度：{len(content_str)} 字符")
        print(f"响应内容预览 (前 800 字符):\n{content_str[:800]}")
        if len(content_str) > 800:
            print(f"\n...\n后 200 字符:\n{content_str[-200:]}")
    print("\n=== 开始结构化输出解析 ===")

    try:
        knowledge_response = diagnostic_knowledge.invoke(
            {
                "retrieved_dsl_list_json": dsl_list_json,
                "count": len(dsl_list),
                "schema": json.dumps(library_schema, indent=2),
            },
            config={"callbacks": [LoggingCallbackHandler()]},
        )

        print(f"\n返回类型：{type(knowledge_response)}")
        print(f"返回值是否为 None: {knowledge_response is None}")

        if knowledge_response:
            print(f"\n成功整合诊断知识库：")
            print(f"检索到的案例数：{len(knowledge_response.retrieved_cases)}")
            print(f"对比摘要数：{len(knowledge_response.comparison_map)}")

            # 保存整合后的知识库
            output_path = "dsl/integrated_knowledge_library.json"
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(knowledge_response.model_dump_json(indent=2))
            print(f"知识库已保存到：{output_path}")
        else:
            print("\n警告：knowledge_response 为 None！")
            print("请检查 LLM 的响应和结构化输出解析")

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
    # fmt: skip
    os.environ["LANGSMITH_API_KEY"] = (
        "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"
    )

    main()
