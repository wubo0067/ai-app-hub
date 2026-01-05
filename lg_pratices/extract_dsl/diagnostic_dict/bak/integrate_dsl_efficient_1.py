import os
import json
import logging
from typing import List, Optional, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from langchain_core.callbacks import BaseCallbackHandler

# 设置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- 借鉴 extract_integrate.py 中的详细 prompt ---
incremental_merge_prompt = ChatPromptTemplate.from_template(
    """You are a Linux Kernel Diagnostic Architect. Build a COMPREHENSIVE decision matrix.

    # Task
    Integrate NEW diagnostic workflows into the existing knowledge base to create a unified, master "Condition -> Action" rule set.
    Your goal is to create a knowledge base that covers ALL distinct scenarios and edge cases found in the input data.

    # Integration Guidelines
    1. **Maximize Coverage**: Do NOT over-simplify. Ensure that every unique diagnostic path and specific check from the inputs is represented in the matrix.
    2. **Preserve Nuance**: If two workflows look similar but check different fields or have different thresholds, create a BRANCH based on the specific symptom or trigger. Do not merge them if it loses technical accuracy.
    3. **Trigger Specificity**: The `trigger` must be specific enough to distinguish between different scenarios (e.g., distinguish "General Panic" from "Null Pointer Dereference").
    4. **Action Precision**: Keep the specific `crash` commands and argument hints accurate. Do not generalize commands to the point of uselessness.
    5. **Consolidation Strategy**: Merge ONLY truly identical steps. If there is a variation, keep both as separate branches.
    6. **Templating: NO hex addresses or specific CPUs. Use {{addr}}, {{cpu}}, {{pid}}, {{offset}}.

    # FIELD CONSTRAINTS (STRICT)
    - trigger: Max 12 words. Specific symptom.
    - arg_hints: Max 10 words. Format: "key: source". Can be null if not applicable.
    - why: Max 15 words. Technical essence.
    - expect: Max 15 words. Key pattern/string.
    - action: REQUIRED string. Even for is_end: true branches, provide a meaningful action or "N/A" if no command needed.
    - is_end: true only for root cause conclusions, false for diagnostic steps.

    # EXAMPLES
    - For diagnostic step: "action": "spinlock_t {{lock_addr}}", "arg_hints": "lock_addr: from RDI or stack", "is_end": false
    - For root cause conclusion: "action": "N/A", "arg_hints": null, "is_end": true, "trigger": "Timer discrepancy with CPU idle in spinlock", "why": "CPU idle while holding spinlock causes timer lag", "expect": "tvec_base lagging on all but lockup CPU"
    - Conciseness: "trigger": "Stack contains native_queued_spin_lock_slowpath (contention)"
    - Conciseness: "why": "Recursive deadlock: current task already holds the lock"

    # CURRENT KNOWLEDGE BASE (Summary)
    {current_kb_summary}

    # NEW DSL CASE (Extracted Key Information)
    {new_case_summary}

    # Output Requirement
    - Output strictly according to the JSON Schema.
    - **Language**: English.
    - **Fix**: Ensure double curly braces are used for placeholders in the prompt examples, but single braces in the JSON output.
    - **Target**: Aim for comprehensive coverage with 35-45 total branches

    # Output JSON Schema
    {schema}
"""
)


# 数据结构定义（与现有脚本保持一致）
class DiagnosticBranch(BaseModel):
    """Simplified diagnostic rule: Condition -> Action."""

    trigger: str = Field(
        ...,
        description="Concise symptom (max 12 words). E.g., 'Watchdog detected hard LOCKUP'.",
    )
    action: str = Field(
        ...,
        description="Command template with placeholders, e.g., 'struct spinlock {addr}'.",
    )
    arg_hints: Optional[str] = Field(
        None,
        description="Short syntax (max 10 words). E.g., 'addr: from RDI register'.",
    )
    why: str = Field(
        ...,
        description="Brief technical reason (max 15 words).",
    )
    expect: str = Field(
        ...,
        description="Key pattern to look for (max 15 words).",
    )
    is_end: bool = Field(
        False,
        description="True if this is a root cause conclusion.",
    )


class DiagnosticDict(BaseModel):
    """Compact diagnostic knowledge base for Hard LOCKUP scenarios."""

    summary: str = "diagnostic matrix for Linux kernel Hard LOCKUP scenarios"
    init_cmds: List[str] = Field(
        ...,
        description="Common initial commands for Hard LOCKUP diagnosis.",
    )
    matrix: List[DiagnosticBranch] = Field(
        ..., description="The decision matrix for diagnosing Hard LOCKUP root causes."
    )


class LoggingCallbackHandler(BaseCallbackHandler):
    """Callback handler for logging LLM operations."""

    def on_llm_start(self, serialized, prompts, **kwargs):
        logger.info(f"LLM 调用开始：{serialized.get('name', 'unknown')}")

    def on_llm_end(self, response, **kwargs):
        logger.info("LLM 调用结束")

    def on_chain_error(self, error, **kwargs):
        logger.error(f"Chain 错误：{error}")


def extract_key_info_from_dsl(dsl_content: str) -> Dict[str, Any]:
    """
    从 DSL JSON 中提取关键信息，减少传递给 LLM 的数据量。
    返回一个简化的摘要，而不是完整的 JSON。
    """
    try:
        dsl_data = json.loads(dsl_content)

        # 提取关键信息
        scenario = dsl_data.get("scenario", "Unknown scenario")
        symptoms = dsl_data.get("symptoms", [])
        workflow_steps = dsl_data.get("workflow", [])
        root_causes = dsl_data.get("root_cause_analysis", [])

        # 提取关键命令和模式 - 提取更多命令以保留细节
        key_commands = []
        key_command_details = []

        # 提取更多步骤，确保覆盖重要命令
        # 对于大型 workflow，提取更多代表性步骤
        max_steps_to_extract = min(35, len(workflow_steps))  # 增加到 35 个步骤
        step_indices = []

        # 选择代表性步骤：前 10 个，中间 15 个，最后 10 个（如果足够长）
        if len(workflow_steps) <= 35:
            step_indices = list(range(len(workflow_steps)))
        else:
            step_indices = (
                list(range(10))  # 前 10 个步骤
                + list(
                    range(
                        10,
                        len(workflow_steps) - 10,
                        max(1, (len(workflow_steps) - 20) // 15),
                    )
                )[
                    :15
                ]  # 中间 15 个代表性步骤
                + list(
                    range(len(workflow_steps) - 10, len(workflow_steps))
                )  # 最后 10 个步骤
            )

        for idx in step_indices[:max_steps_to_extract]:
            step = workflow_steps[idx]
            if "action" in step:
                action = step["action"]
                key_commands.append(action)

                # 同时提取 thought 和 observation 用于上下文
                thought = step.get("thought", "")
                observation = step.get("observation", "")
                if thought or observation:
                    key_command_details.append(
                        {
                            "step_number": step.get("step_number", idx + 1),
                            "action": action,
                            "thought": (
                                thought[:100] + "..." if len(thought) > 100 else thought
                            ),
                            "observation": (
                                observation[:100] + "..."
                                if len(observation) > 100
                                else observation
                            ),
                        }
                    )

        # 构建更详细的摘要
        summary = {
            "scenario": scenario,
            "symptom_count": len(symptoms),
            "key_symptoms": (
                symptoms[:20] if len(symptoms) > 20 else symptoms
            ),  # 增加到 20 个症状
            "workflow_step_count": len(workflow_steps),
            "key_commands": key_commands[:30],  # 增加到 30 个关键命令
            "key_command_details": key_command_details[:25],  # 增加到 25 个命令详情
            "root_cause_count": len(root_causes),
            "key_root_causes": (
                root_causes[:12] if root_causes else []
            ),  # 增加到 12 个根因
            "representative_actions": [],  # 用于识别不同类型的命令
        }

        # 识别代表性的命令类型
        command_types = set()
        for cmd in key_commands:
            if "bt" in cmd:
                command_types.add("backtrace_commands")
            elif "log" in cmd or "grep" in cmd:
                command_types.add("log_analysis")
            elif "ps" in cmd or "task" in cmd:
                command_types.add("process_analysis")
            elif "struct" in cmd or "px" in cmd or "spinlock" in cmd:
                command_types.add("memory_structure_analysis")
            elif "kmem" in cmd or "mem" in cmd:
                command_types.add("memory_analysis")
            elif "dis" in cmd:
                command_types.add("disassembly")

        summary["command_categories"] = list(command_types)

        return summary
    except Exception as e:
        logger.error(f"提取 DSL 关键信息失败：{e}")
        # 如果解析失败，返回原始内容的简化版本
        return {
            "scenario": "Parsing failed",
            "raw_content_preview": (
                dsl_content[:1000] + "..."
                if len(dsl_content) > 1000
                else dsl_content  # 增加到 1000 字符
            ),
        }


def summarize_knowledge_base(kb: Optional[DiagnosticDict]) -> str:
    """
    生成知识库的摘要，而不是传递完整 JSON。
    """
    if kb is None:
        return "Empty knowledge base (initial state)"

    summary = f"""
Knowledge Base Summary:
- Total branches: {len(kb.matrix)}
- Init commands: {len(kb.init_cmds)}
- Recent branches (last 5):
"""

    for i, branch in enumerate(kb.matrix[-5:] if len(kb.matrix) >= 5 else kb.matrix):
        summary += f"  {i+1}. Trigger: {branch.trigger[:50]}...\n"

    return summary


def batch_integrate_dsl_files(
    dsl_files: List[str],
    batch_size: int = 3,
    output_file: str = "efficient_diagnostic_knowledge_library.json",
) -> Optional[DiagnosticDict]:
    """
    分批整合 DSL 文件，避免 token 限制问题。

    Args:
        dsl_files: DSL 文件路径列表
        batch_size: 每批处理的文件数量
        output_file: 输出文件名

    Returns:
        整合后的 DiagnosticDict 对象
    """
    logger.info(f"开始分批整合 {len(dsl_files)} 个 DSL 文件，批次大小：{batch_size}")

    # 初始化 LLM
    llm = ChatOpenAI(
        api_key="sk-b5480f840a794c69a0af1732459f3ae4",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0,
    )

    # 创建整合链
    dd_schema = DiagnosticDict.model_json_schema()
    chain = incremental_merge_prompt | llm.with_structured_output(
        DiagnosticDict, method="json_mode"
    )

    current_kb = None

    # 将文件分成批次
    batches = [
        dsl_files[i : i + batch_size] for i in range(0, len(dsl_files), batch_size)
    ]

    for batch_num, batch_files in enumerate(batches, 1):
        logger.info(f"处理批次 {batch_num}/{len(batches)}: {len(batch_files)} 个文件")

        # 读取并预处理本批次的所有文件
        batch_summaries = []
        batch_contents = []

        for file_path in batch_files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    batch_contents.append(content)

                    # 提取关键信息
                    summary = extract_key_info_from_dsl(content)
                    batch_summaries.append(summary)

                    logger.info(f"  已读取：{os.path.basename(file_path)}")
            except Exception as e:
                logger.error(f"  读取文件失败 {file_path}: {e}")
                continue

        if not batch_contents:
            logger.warning(f"批次 {batch_num} 没有有效内容，跳过")
            continue

        # 合并本批次的所有摘要 - 包含更多细节
        combined_summary = {
            "batch_size": len(batch_contents),
            "scenarios": [s.get("scenario", "Unknown") for s in batch_summaries],
            "total_symptoms": sum(s.get("symptom_count", 0) for s in batch_summaries),
            "total_workflow_steps": sum(
                s.get("workflow_step_count", 0) for s in batch_summaries
            ),
            "key_commands": [],
            "key_command_details": [],
            "key_root_causes": [],
            "command_categories": set(),
        }

        # 收集所有关键命令、命令详情、根因和命令类别
        for summary in batch_summaries:
            combined_summary["key_commands"].extend(summary.get("key_commands", []))
            combined_summary["key_command_details"].extend(
                summary.get("key_command_details", [])
            )
            combined_summary["key_root_causes"].extend(
                summary.get("key_root_causes", [])
            )

            # 合并命令类别
            categories = summary.get("command_categories", [])
            for category in categories:
                combined_summary["command_categories"].add(category)

        # 去重和限制数量 - 增加限制以保留更多细节
        combined_summary["key_commands"] = list(set(combined_summary["key_commands"]))[
            :40  # 增加到 40 个命令
        ]
        combined_summary["key_command_details"] = combined_summary[
            "key_command_details"
        ][
            :30
        ]  # 增加到 30 个命令详情
        combined_summary["key_root_causes"] = list(
            set(combined_summary["key_root_causes"])
        )[
            :15
        ]  # 增加到 15 个根因

        # 转换 set 为 list
        combined_summary["command_categories"] = list(
            combined_summary["command_categories"]
        )

        # 转换为 JSON 字符串用于 prompt
        new_case_json = json.dumps(combined_summary, ensure_ascii=False, indent=2)

        # 生成当前知识库摘要
        current_kb_summary = summarize_knowledge_base(current_kb)

        try:
            logger.info(f"调用 LLM 整合批次 {batch_num}...")

            # 调用 LLM 进行整合
            new_kb = chain.invoke(
                {
                    "current_kb_summary": current_kb_summary,
                    "new_case_summary": new_case_json,
                    "schema": json.dumps(dd_schema, indent=2),
                },
                config={"callbacks": [LoggingCallbackHandler()]},
            )

            current_kb = new_kb
            logger.info(
                f"批次 {batch_num} 整合成功。当前知识库：{len(current_kb.matrix)} 个分支"
            )

        except Exception as e:
            logger.error(f"批次 {batch_num} 整合失败：{e}")
            # 继续处理下一个批次，而不是完全失败

    # 保存最终结果
    if current_kb:
        try:
            output_path = os.path.join("dsl", output_file)
            diagnostic_dict_json = current_kb.model_dump_json(indent=2)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(diagnostic_dict_json)

            logger.info(f"知识库已保存至：{output_path}")
            logger.info(f"最终统计：{len(current_kb.matrix)} 个诊断分支")
            logger.info(f"初始命令：{len(current_kb.init_cmds)} 个")

            return current_kb
        except Exception as e:
            logger.error(f"保存结果失败：{e}")

    return None


def main():
    """主函数"""
    # 确保 dsl 目录存在
    os.makedirs("dsl", exist_ok=True)

    # DSL 文件列表（可以根据需要修改）
    dsl_files = [
        "dsl/3379041.json",
        "dsl/3870151.json",
        "dsl/6348992.json",
        "dsl/6988986.json",
        "dsl/5764681.json",
        "dsl/7041099.json",
    ]

    # 检查文件是否存在
    existing_files = []
    for file_path in dsl_files:
        if os.path.exists(file_path):
            existing_files.append(file_path)
        else:
            logger.warning(f"文件不存在：{file_path}")

    if not existing_files:
        logger.error("没有找到可用的 DSL 文件")
        return

    logger.info(f"找到 {len(existing_files)} 个 DSL 文件")

    # 执行分批整合 - 使用批次大小为 1 确保每个文件都被充分处理
    result = batch_integrate_dsl_files(
        dsl_files=existing_files,
        batch_size=1,  # 每批 1 个文件，确保充分提取每个文件的细节
        output_file="efficient_diagnostic_knowledge_library.json",
    )

    if result:
        logger.info("整合完成！")
        print(f"\n{'='*60}")
        print("整合结果摘要：")
        print(f"{'='*60}")
        print(f"知识库总结：{result.summary}")
        print(f"初始命令数量：{len(result.init_cmds)}")
        print(f"诊断分支数量：{len(result.matrix)}")
        print(f"\n前 5 个诊断分支：")
        for i, branch in enumerate(result.matrix[:5]):
            print(f"  {i+1}. {branch.trigger[:50]}...")
    else:
        logger.error("整合失败")


if __name__ == "__main__":
    # 设置环境变量
    os.environ["TAVILY_API_KEY"] = "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"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGSMITH_API_KEY"] = (
        "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"
    )

    main()
