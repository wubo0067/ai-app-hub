import os
import json
import logging
import re
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.callbacks import BaseCallbackHandler

# 设置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================================
# Pydantic 数据模型定义
# ============================================================================


class DiagnosticBranch(BaseModel):
    """Diagnostic rule: Condition -> Action."""

    trigger: str = Field(
        ...,
        description="Concise symptom description (max 18 words). Must be SPECIFIC enough to distinguish from similar branches.",
    )
    action: str = Field(
        ...,
        description="Complete command template with placeholders. Use {{cpu}}, {{addr}}, {{pid}}, {{offset}}, {{modname}} etc. for variables.",
    )
    arg_hints: Optional[str] = Field(
        None,
        description="Parameter sources (max 15 words). Example: 'cpu: from lockup message, addr: from RDI register'.",
    )
    why: str = Field(
        ...,
        description="Diagnostic purpose (max 20 words). Explain WHAT this step checks for.",
    )
    expect: str = Field(
        ...,
        description="Expected output pattern (max 20 words). Be specific about what to look for.",
    )
    is_end: bool = Field(
        False,
        description="True if this is a definitive diagnostic conclusion. For root cause analysis, set to true.",
    )


class DiagnosticKnowledge(BaseModel):
    """Comprehensive diagnostic knowledge base for Hard LOCKUP vmcore analysis."""

    summary: str = Field(
        "Comprehensive diagnostic matrix for Linux kernel Hard LOCKUP scenarios",
        description="Fixed summary for the diagnostic knowledge base.",
    )
    init_cmds: List[str] = Field(
        ...,
        description="Common initial diagnostic commands (8-12 commands). Should be generic commands for initial investigation.",
    )
    matrix: List[DiagnosticBranch] = Field(
        ...,
        description="Complete diagnostic decision matrix covering all workflow thoughts from DSL files.",
    )


# ============================================================================
# LLM Prompt 模板
# ============================================================================

DIAGNOSTIC_INTEGRATION_PROMPT = ChatPromptTemplate.from_template(
    """You are a Linux Kernel Diagnostic Architect creating a vmcore diagnostic knowledge base.

# MISSION
Extract and integrate diagnostic knowledge from Linux kernel hard lockup analysis workflows into a structured diagnostic matrix.

# CRITICAL RULES
1. **NO HARDCODED VALUES**: Replace ALL specific values with placeholders:
   - Addresses: 0xffff88... → {{addr}}
   - CPU numbers: cpu 3 → cpu {{cpu}}
   - PID values: PID 11361 → PID {{pid}}
   - Offsets: offset 0x68 → offset {{offset}}
   - Module names: [splxmod] → [{{modname}}]
   - Device names: /dev/vda → /dev/{{device}}

2. **STRUCTURED OUTPUT**: Output must conform to the provided Pydantic BaseModel schema.

3. **COMPREHENSIVE COVERAGE**: Extract diagnostic knowledge from EVERY thought in the workflow steps.
   - Each unique diagnostic thought should generate at least one diagnostic branch
   - Preserve the diagnostic logic and decision flow
   - Cover all types of checks: backtraces, memory examination, structure analysis, log analysis, etc.

4. **FIELD LENGTH CONSTRAINTS**:
   - trigger: Max 18 words, SPECIFIC enough to distinguish from similar branches
   - action: Complete command with placeholders
   - arg_hints: Max 15 words
   - why: Max 20 words
   - expect: Max 20 words

5. **INITIAL COMMANDS**: Generate 8-12 common initial diagnostic commands that would be useful for any hard lockup analysis.

6. **NO ROOT CAUSE EXTRACTION**: Do NOT extract root cause analysis from the DSL files. Focus only on diagnostic steps.

# INTEGRATION STRATEGY
1. **Group similar diagnostic thoughts**: Similar checks can be merged if they serve the same diagnostic purpose
2. **Preserve diagnostic intent**: Ensure the 'why' field clearly explains what the step is checking for
3. **Maintain action specificity**: Keep commands specific to what they examine
4. **Use appropriate placeholders**: Replace instance-specific values with generic placeholders

# OUTPUT FORMAT
You must output a valid JSON object that matches the DiagnosticKnowledge schema.

# SOURCE WORKFLOWS
{workflow_summary}

# SCHEMA
{schema}

# EXAMPLES

## Good Diagnostic Branch:
{{
  "trigger": "Watchdog detected hard LOCKUP on cpu {{cpu}}",
  "action": "bt -c {{cpu}}",
  "arg_hints": "cpu: CPU number from lockup message",
  "why": "Examine what's running on the locked CPU",
  "expect": "Stack trace showing stuck function or spinlock",
  "is_end": false
}}

## Good Structure Examination:
{{
  "trigger": "Need to examine spinlock state from backtrace",
  "action": "arch_spinlock {{addr}}",
  "arg_hints": "addr: spinlock address from RDI register in backtrace",
  "why": "Check spinlock contention state and waiter information",
  "expect": "Spinlock head/tail values showing contention pattern",
  "is_end": false
}}

## Good Log Analysis:
{{
  "trigger": "Search for lockup-related messages in kernel log",
  "action": "log | grep -i -e lockup -e LOCKUP -e rcu_sched",
  "arg_hints": null,
  "why": "Find lockup detection messages and RCU stall warnings",
  "expect": "Lockup messages with CPU numbers and timestamps",
  "is_end": false
}}

## BAD Example (Avoid This):
{{
  "trigger": "Check something",  # Too vague
  "action": "bt",  # Missing placeholders for specificity
  "why": "Look at backtrace",  # Not explaining diagnostic purpose
  "expect": "Some output",  # Not specific enough
  "is_end": false
}}

Now, create a comprehensive diagnostic knowledge base from the provided workflows.
"""
)


# ============================================================================
# 工具函数
# ============================================================================


class LoggingCallbackHandler(BaseCallbackHandler):
    """Callback handler for logging LLM operations."""

    def on_llm_start(self, serialized, prompts, **kwargs):
        logger.info(f"LLM 调用开始：{serialized.get('name', 'unknown')}")

    def on_llm_end(self, response, **kwargs):
        logger.info("LLM 调用结束")

    def on_llm_error(self, error, **kwargs):
        logger.error(f"LLM 错误：{error}", exc_info=True)


def extract_workflow_data(dsl_content: str) -> Dict[str, Any]:
    """
    从 DSL JSON 内容中提取工作流数据。

    Args:
        dsl_content: DSL JSON 文件内容

    Returns:
        包含工作流信息的字典
    """
    try:
        data = json.loads(dsl_content)

        # 提取工作流步骤
        workflow_steps = data.get("workflow", [])

        # 提取每个步骤的 thought 和 action
        steps_info = []
        for step in workflow_steps:
            thought = step.get("thought", "")
            action = step.get("action", "")

            if thought and action:
                steps_info.append(
                    {
                        "thought": thought,
                        "action": action,
                        "observation_preview": step.get("observation", "")[:100],
                    }
                )

        # 提取症状（用于上下文）
        symptoms = data.get("symptoms", [])

        return {
            "scenario": data.get("scenario", "Unknown scenario"),
            "symptoms": symptoms,
            "total_steps": len(workflow_steps),
            "extracted_steps": steps_info,
            "step_count": len(steps_info),
        }

    except json.JSONDecodeError as e:
        logger.error(f"JSON 解析错误：{e}")
        return {"error": f"JSON 解析失败：{str(e)}"}
    except Exception as e:
        logger.error(f"工作流数据提取错误：{e}", exc_info=True)
        return {"error": f"提取失败：{str(e)}"}


def chunk_workflows(
    workflows_data: List[Dict[str, Any]], max_tokens: int = 8000
) -> List[List[Dict[str, Any]]]:
    """
    将工作流数据分块以处理 token 限制。

    Args:
        workflows_data: 工作流数据列表
        max_tokens: 每个块的最大 token 数估计

    Returns:
        分块后的工作流数据
    """
    chunks = []
    current_chunk = []
    current_size = 0

    for workflow in workflows_data:
        # 估算当前工作流的大小
        workflow_json = json.dumps(workflow, ensure_ascii=False)
        workflow_tokens = len(workflow_json) // 4

        # 如果当前块为空或添加后不会超过限制，则添加到当前块
        if current_size == 0 or current_size + workflow_tokens <= max_tokens:
            current_chunk.append(workflow)
            current_size += workflow_tokens
        else:
            # 开始新块
            chunks.append(current_chunk)
            current_chunk = [workflow]
            current_size = workflow_tokens

    # 添加最后一个块
    if current_chunk:
        chunks.append(current_chunk)

    logger.info(f"将 {len(workflows_data)} 个工作流分成了 {len(chunks)} 个块")
    return chunks


def create_workflow_summary(workflows: List[Dict[str, Any]]) -> str:
    """
    创建工作流摘要用于 LLM 提示。

    Args:
        workflows: 工作流数据列表

    Returns:
        格式化的摘要字符串
    """
    summary_parts = []

    for i, workflow in enumerate(workflows):
        if "error" in workflow:
            continue

        summary_parts.append(
            f"## Workflow {i+1}: {workflow.get('scenario', 'Unknown')}"
        )
        summary_parts.append(
            f"Symptoms: {', '.join(workflow.get('symptoms', [])[:3])}{'...' if len(workflow.get('symptoms', [])) > 3 else ''}"
        )
        summary_parts.append(f"Total steps: {workflow.get('total_steps', 0)}")
        summary_parts.append(
            f"Extracted diagnostic steps: {workflow.get('step_count', 0)}"
        )

        # 添加前几个步骤作为示例
        steps = workflow.get("extracted_steps", [])
        if steps:
            summary_parts.append("Sample diagnostic thoughts:")
            for j, step in enumerate(steps[:3]):
                summary_parts.append(f"  {j+1}. Thought: {step['thought']}")
                summary_parts.append(f"     Action: {step['action']}")

        summary_parts.append("")  # 空行分隔

    return "\n".join(summary_parts)


def merge_diagnostic_knowledge(
    knowledge_chunks: List[DiagnosticKnowledge],
) -> DiagnosticKnowledge:
    """
    合并多个知识块。

    Args:
        knowledge_chunks: 多个知识库块

    Returns:
        合并后的知识库
    """
    if not knowledge_chunks:
        return DiagnosticKnowledge(
            summary="Comprehensive diagnostic matrix for Linux kernel Hard LOCKUP scenarios",
            init_cmds=[],
            matrix=[],
        )

    # 使用第一个块作为基础
    merged = knowledge_chunks[0]

    # 合并后续块
    for chunk in knowledge_chunks[1:]:
        # 合并 init_cmds（去重）
        for cmd in chunk.init_cmds:
            if cmd not in merged.init_cmds:
                merged.init_cmds.append(cmd)

        # 合并 matrix（基于 trigger 去重）
        existing_triggers = {branch.trigger for branch in merged.matrix}
        for branch in chunk.matrix:
            if branch.trigger not in existing_triggers:
                merged.matrix.append(branch)
                existing_triggers.add(branch.trigger)

    # 限制 init_cmds 数量为 8-12
    if len(merged.init_cmds) > 12:
        merged.init_cmds = merged.init_cmds[:12]
    elif len(merged.init_cmds) < 8:
        # 如果不足 8 个，添加一些通用命令
        default_cmds = [
            "sys -i",
            "bt -a",
            "log | grep -i lockup",
            "ps -m",
            "kmem -i",
            "runq -a",
            "dev -d",
            "mod",
        ]
        for cmd in default_cmds:
            if cmd not in merged.init_cmds and len(merged.init_cmds) < 8:
                merged.init_cmds.append(cmd)

    return merged


def validate_diagnostic_knowledge(knowledge: DiagnosticKnowledge) -> Dict[str, Any]:
    """
    验证诊断知识库的质量。

    Args:
        knowledge: 诊断知识库

    Returns:
        验证结果
    """
    issues = []
    warnings = []

    # 检查 init_cmds 数量
    init_cmd_count = len(knowledge.init_cmds)
    if init_cmd_count < 8:
        issues.append(f"init_cmds 数量不足 ({init_cmd_count})，需要 8-12 个")
    elif init_cmd_count > 12:
        warnings.append(f"init_cmds 数量过多 ({init_cmd_count})，建议 8-12 个")

    # 检查 matrix 数量
    matrix_count = len(knowledge.matrix)
    if matrix_count < 20:
        warnings.append(f"诊断分支数量较少 ({matrix_count})，建议更多以覆盖全面")

    # 检查字段长度
    for i, branch in enumerate(knowledge.matrix):
        # 检查 trigger 长度
        trigger_words = len(branch.trigger.split())
        if trigger_words > 18:
            warnings.append(f"分支 {i+1} trigger 过长 ({trigger_words}个单词)")

        # 检查 arg_hints 长度
        if branch.arg_hints:
            arg_words = len(branch.arg_hints.split())
            if arg_words > 15:
                warnings.append(f"分支 {i+1} arg_hints 过长 ({arg_words}个单词)")

        # 检查 why 长度
        why_words = len(branch.why.split())
        if why_words > 20:
            warnings.append(f"分支 {i+1} why 过长 ({why_words}个单词)")

        # 检查 expect 长度
        expect_words = len(branch.expect.split())
        if expect_words > 20:
            warnings.append(f"分支 {i+1} expect 过长 ({expect_words}个单词)")

        # 检查硬编码值
        hardcoded_patterns = [
            r"0x[0-9a-f]{8,}",
            r"cpu\s+\d+",
            r"PID\s+\d+",
            r"offset\s+0x[0-9a-f]+",
            r"\[[a-zA-Z0-9_]+\]",
        ]

        for pattern in hardcoded_patterns:
            if re.search(pattern, branch.action):
                warnings.append(
                    f"分支 {i+1} action 中可能包含硬编码值：{branch.action[:50]}..."
                )
                break

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "statistics": {
            "init_cmds": init_cmd_count,
            "matrix_branches": matrix_count,
            "end_branches": len([b for b in knowledge.matrix if b.is_end]),
            "diagnostic_branches": len([b for b in knowledge.matrix if not b.is_end]),
        },
    }


# ============================================================================
# 主整合函数
# ============================================================================


def integrate_dsl_files(
    dsl_dir: str,
    output_file: str = "diagnostic_knowledge_library.json",
    api_key: Optional[str] = None,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-chat",
) -> Optional[DiagnosticKnowledge]:
    """
    整合 DSL 文件生成诊断知识库。

    Args:
        dsl_dir: DSL 文件目录
        output_file: 输出文件名
        api_key: DeepSeek API 密钥
        base_url: API 基础 URL
        model: 模型名称

    Returns:
        诊断知识库对象，失败时返回 None
    """
    logger.info(f"开始整合 DSL 文件，目录：{dsl_dir}")

    # 检查 DSL 目录
    if not os.path.exists(dsl_dir):
        logger.error(f"DSL 目录不存在：{dsl_dir}")
        return None

    # 获取所有 JSON 文件
    dsl_files = []
    for file in os.listdir(dsl_dir):
        if file.endswith(".json"):
            dsl_files.append(os.path.join(dsl_dir, file))

    if not dsl_files:
        logger.error(f"在目录 {dsl_dir} 中没有找到 JSON 文件")
        return None

    logger.info(f"找到 {len(dsl_files)} 个 DSL 文件")

    # 提取所有工作流数据
    workflows_data = []
    for file_path in dsl_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            workflow_data = extract_workflow_data(content)
            if "error" not in workflow_data:
                workflows_data.append(workflow_data)
                logger.info(
                    f"  已处理：{os.path.basename(file_path)} ({workflow_data['step_count']} 个诊断步骤)"
                )
            else:
                logger.warning(
                    f"  处理失败：{os.path.basename(file_path)}: {workflow_data.get('error', 'Unknown error')}"
                )
        except Exception as e:
            logger.warning(f"  读取文件失败 {os.path.basename(file_path)}: {e}")

    if not workflows_data:
        logger.error("没有成功提取任何工作流数据")
        return None

    logger.info(
        f"成功提取 {len(workflows_data)} 个工作流数据，总计 {sum(w.get('step_count', 0) for w in workflows_data)} 个诊断步骤"
    )

    # 分块处理（如果需要）
    chunks = chunk_workflows(workflows_data)

    # 初始化 LLM
    if api_key is None:
        logger.error("需要提供 API 密钥")
        return None

    llm = ChatOpenAI(
        api_key=api_key, base_url=base_url, model=model, temperature=0, streaming=False
    )

    # 创建处理链
    schema_json = json.dumps(DiagnosticKnowledge.model_json_schema(), indent=2)
    chain = DIAGNOSTIC_INTEGRATION_PROMPT | llm.with_structured_output(
        DiagnosticKnowledge, method="json_mode"
    )

    # 处理每个块
    knowledge_chunks = []

    for i, chunk in enumerate(chunks):
        logger.info(f"处理块 {i+1}/{len(chunks)}，包含 {len(chunk)} 个工作流")

        # 创建工作流摘要
        workflow_summary = create_workflow_summary(chunk)

        try:
            # 调用 LLM
            knowledge_chunk = chain.invoke(
                {"workflow_summary": workflow_summary, "schema": schema_json},
                config={"callbacks": [LoggingCallbackHandler()]},
            )

            knowledge_chunks.append(knowledge_chunk)
            logger.info(
                f"  块 {i+1} 处理成功，生成 {len(knowledge_chunk.matrix)} 个诊断分支"
            )

        except Exception as e:
            logger.error(f"  块 {i+1} 处理失败：{e}", exc_info=True)
            # 继续处理其他块

    if not knowledge_chunks:
        logger.error("所有块处理都失败")
        return None

    # 合并所有块
    merged_knowledge = merge_diagnostic_knowledge(knowledge_chunks)
    logger.info(
        f"合并后总计：{len(merged_knowledge.init_cmds)} 个初始命令，{len(merged_knowledge.matrix)} 个诊断分支"
    )

    # 验证结果
    validation = validate_diagnostic_knowledge(merged_knowledge)
    if validation["issues"]:
        logger.warning(f"验证发现问题：{validation['issues']}")
    if validation["warnings"]:
        logger.info(f"验证警告：{validation['warnings'][:3]}")  # 只显示前 3 个警告

    # 保存结果
    output_path = os.path.join(os.path.dirname(dsl_dir), output_file)
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(merged_knowledge.model_dump_json(indent=2))
        logger.info(f"诊断知识库已保存到：{output_path}")
    except Exception as e:
        logger.error(f"保存文件失败：{e}")
        return None

    return merged_knowledge


# ============================================================================
# 命令行接口
# ============================================================================


def main():
    """主函数：命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="整合 DSL 文件生成 vmcore 诊断知识库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  %(prog)s --dsl-dir ../dsl --output diagnostic_knowledge.json
  %(prog)s --dsl-dir ../dsl --api-key your-api-key
        """,
    )

    parser.add_argument(
        "--dsl-dir",
        type=str,
        default="../dsl",
        help="DSL JSON 文件目录（默认：../dsl）",
    )

    parser.add_argument(
        "--output",
        type=str,
        default="diagnostic_knowledge_library.json",
        help="输出文件名（默认：diagnostic_knowledge_library.json）",
    )

    parser.add_argument(
        "--api-key",
        type=str,
        help="DeepSeek API 密钥。如果未提供，将尝试从环境变量 DEEPSEEK_API_KEY 读取",
    )

    parser.add_argument(
        "--base-url",
        type=str,
        default="https://api.deepseek.com",
        help="API 基础 URL（默认：https://api.deepseek.com）",
    )

    parser.add_argument(
        "--model",
        type=str,
        default="deepseek-chat",
        help="模型名称（默认：deepseek-chat）",
    )

    parser.add_argument("--verbose", action="store_true", help="启用详细日志输出")

    args = parser.parse_args()

    # 设置日志级别
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # 获取 API 密钥
    api_key = args.api_key
    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            logger.error(
                "需要提供 API 密钥。请通过--api-key 参数或设置 DEEPSEEK_API_KEY 环境变量"
            )
            return 1

    # 检查 DSL 目录
    dsl_dir = args.dsl_dir
    if not os.path.exists(dsl_dir):
        logger.error(f"DSL 目录不存在：{dsl_dir}")
        logger.info(f"当前工作目录：{os.getcwd()}")
        return 1

    logger.info(f"开始整合 DSL 文件")
    logger.info(f"  DSL 目录：{dsl_dir}")
    logger.info(f"  输出文件：{args.output}")
    logger.info(f"  模型：{args.model}")

    # 执行整合
    result = integrate_dsl_files(
        dsl_dir=dsl_dir,
        output_file=args.output,
        api_key=api_key,
        base_url=args.base_url,
        model=args.model,
    )

    if result:
        logger.info("整合成功完成！")

        # 显示统计信息
        print(f"\n{'='*60}")
        print("📊 诊断知识库统计")
        print(f"{'='*60}")
        print(f"总结：{result.summary}")
        print(f"初始命令数量：{len(result.init_cmds)}")
        print(f"诊断分支总数：{len(result.matrix)}")

        # 分类统计
        diagnostic_branches = [b for b in result.matrix if not b.is_end]
        end_branches = [b for b in result.matrix if b.is_end]

        print(f"  诊断步骤：{len(diagnostic_branches)}")
        print(f"  结论分支：{len(end_branches)}")

        # 显示初始命令
        print(f"\n📋 初始诊断命令：")
        for i, cmd in enumerate(result.init_cmds, 1):
            print(f"  {i:2d}. {cmd}")

        # 显示示例分支
        print(f"\n🔍 示例诊断分支 (前 5 个):")
        for i, branch in enumerate(result.matrix[:5], 1):
            end_marker = " [结论]" if branch.is_end else ""
            print(f"\n  {i}. {branch.trigger}{end_marker}")
            print(
                f"     命令：{branch.action[:60]}{'...' if len(branch.action) > 60 else ''}"
            )
            print(f"     目的：{branch.why}")

        print(
            f"\n💾 结果已保存到：{os.path.join(os.path.dirname(dsl_dir), args.output)}"
        )
        print(f"{'='*60}")

        return 0
    else:
        logger.error("整合失败")
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
