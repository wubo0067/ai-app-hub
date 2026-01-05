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
        description="Common initial diagnostic commands (2-3 commands). Should be generic commands for initial investigation.",
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
Extract diagnostic knowledge from Linux kernel hard lockup analysis workflows into a structured diagnostic matrix.

# CRITICAL RULES
1. **ACTION EXTRACTION FROM DSL ONLY**: Actions MUST be extracted STRICTLY from the DSL JSON files. Do NOT invent new commands or use system commands (like cat, ps, iostat, ifconfig, df, mpstat, etc.). Only use crash utility commands found in the DSL files (e.g., bt, kmem, struct, px, log, sys, etc.).
2. **NO HARDCODED VALUES**: Replace specific values with placeholders: {{addr}}, {{cpu}}, {{pid}}, {{offset}}, {{modname}}, {{device}}
3. **STRUCTURED OUTPUT**: Output must conform to the provided Pydantic BaseModel schema.
4. **MAXIMUM COVERAGE**: Extract knowledge from EVERY thought in workflow steps. Generate at least one diagnostic branch per thought.
5. **FIELD LENGTH CONSTRAINTS**:
   - trigger: Max 18 words, specific
   - arg_hints: Max 15 words
   - why: Max 20 words
   - expect: Max 20 words
6. **INITIAL COMMANDS**: Generate 2-3 common initial diagnostic commands.
7. **NO ROOT CAUSE EXTRACTION**: Focus only on diagnostic steps.
8. **ACTION DEDUPLICATION**: DO NOT generate duplicate actions. If multiple thoughts suggest the same action, merge them into a single diagnostic branch with a comprehensive trigger that covers all related symptoms.

# INTEGRATION STRATEGY
- Merge only virtually identical thoughts
- Preserve diagnostic intent in 'why' field
- Keep commands specific - use EXACT commands from DSL files
- Use appropriate placeholders
- **CRITICAL**: Avoid duplicate actions - each action should appear only once in the matrix

# OUTPUT FORMAT
Output a valid JSON object matching the DiagnosticKnowledge schema.

# SOURCE WORKFLOWS
{workflow_summary}

# SCHEMA
{schema}

# EXAMPLES (One good example is sufficient)
{{
  "trigger": "Watchdog detected hard LOCKUP on cpu {{cpu}}",
  "action": "bt -c {{cpu}}",
  "arg_hints": "cpu: CPU number from lockup message",
  "why": "Examine what's running on the locked CPU",
  "expect": "Stack trace showing stuck function or spinlock",
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
    workflows_data: List[Dict[str, Any]],
    max_total_tokens: int = 8000,
    prompt_tokens: int = 1500,
) -> List[List[Dict[str, Any]]]:
    """
    将工作流数据分块以处理 token 限制，考虑提示词本身的长度。

    Args:
        workflows_data: 工作流数据列表
        max_total_tokens: 每个块的最大总 token 数（包括提示词和响应）
        prompt_tokens: 提示词本身的 token 数估计

    Returns:
        分块后的工作流数据
    """
    # 为响应留出空间（假设响应最多占剩余空间的 1/3）
    max_workflow_tokens = (max_total_tokens - prompt_tokens) // 3

    chunks = []
    current_chunk = []
    current_size = 0

    for workflow in workflows_data:
        # 估算当前工作流的大小
        workflow_json = json.dumps(workflow, ensure_ascii=False)
        workflow_tokens = len(workflow_json) // 4

        # 如果当前块为空或添加后不会超过限制，则添加到当前块
        if current_size == 0 or current_size + workflow_tokens <= max_workflow_tokens:
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

    logger.info(
        f"将 {len(workflows_data)} 个工作流分成了 {len(chunks)} 个块，每块最多 {max_workflow_tokens} tokens"
    )
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
            f"Symptoms: {', '.join(workflow.get('symptoms', [])[:5])}{'...' if len(workflow.get('symptoms', [])) > 5 else ''}"
        )
        summary_parts.append(f"Total steps: {workflow.get('total_steps', 0)}")
        summary_parts.append(
            f"Extracted diagnostic steps: {workflow.get('step_count', 0)}"
        )

        # 添加更多步骤作为示例，确保覆盖不同类型的诊断思想
        steps = workflow.get("extracted_steps", [])
        if steps:
            summary_parts.append(
                "Sample diagnostic thoughts (ensuring maximum coverage):"
            )

            # 分类步骤以便更好地展示多样性
            step_categories = {
                "system_info": [],
                "backtrace": [],
                "memory": [],
                "process": [],
                "log": [],
                "structure": [],
                "other": [],
            }

            # 分类步骤
            for step in steps:
                thought = step.get("thought", "").lower()
                action = step.get("action", "").lower()

                if any(
                    # 遍历关键字列表 ["sys", "system", "hardware", "bios", "dmi"]
                    # 对每个 word，检查 word in thought or word in action，只要有一个关键字出现，any() 就返回 True
                    word in thought or word in action
                    for word in ["sys", "system", "hardware", "bios", "dmi"]
                ):
                    step_categories["system_info"].append(step)
                elif any(
                    word in thought or word in action
                    for word in ["bt", "backtrace", "stack", "trace"]
                ):
                    step_categories["backtrace"].append(step)
                elif any(
                    word in thought or word in action
                    for word in ["memory", "kmem", "rd", "dump", "page"]
                ):
                    step_categories["memory"].append(step)
                elif any(
                    word in thought or word in action
                    for word in ["ps", "process", "task", "pid", "thread"]
                ):
                    step_categories["process"].append(step)
                elif any(
                    word in thought or word in action
                    for word in ["log", "grep", "search", "message"]
                ):
                    step_categories["log"].append(step)
                elif any(
                    word in thought or word in action
                    for word in [
                        "struct",
                        "px",
                        "arch_spinlock",
                        "spinlock",
                        "wait_bit",
                    ]
                ):
                    step_categories["structure"].append(step)
                else:
                    step_categories["other"].append(step)

            # 从每个类别中选择代表性步骤，确保总步骤数不超过 6 个（减少 token 使用）
            max_total_steps = min(6, len(steps))
            selected_steps = []

            # 首先确保每个非空类别至少有一个代表
            for category in step_categories:
                if step_categories[category] and len(selected_steps) < max_total_steps:
                    # 取该类别的第一个步骤
                    selected_steps.append(step_categories[category][0])

            # 如果还有空间，从所有步骤中均匀选择
            if len(selected_steps) < max_total_steps:
                # 计算每个步骤应该选择的间隔
                step_interval = max(
                    1, len(steps) // (max_total_steps - len(selected_steps))
                )
                for idx in range(0, len(steps), step_interval):
                    if len(selected_steps) >= max_total_steps:
                        break
                    step = steps[idx]
                    # 避免重复
                    if step not in selected_steps:
                        selected_steps.append(step)

            # 显示选中的步骤（简化格式，减少 token 使用）
            for j, step in enumerate(selected_steps[:max_total_steps]):
                # 简化显示格式，减少不必要的文本
                thought_preview = (
                    step["thought"][:80] + "..."
                    if len(step["thought"]) > 80
                    else step["thought"]
                )
                action_preview = (
                    step["action"][:60] + "..."
                    if len(step["action"]) > 60
                    else step["action"]
                )
                summary_parts.append(f"  {j+1}. Thought: {thought_preview}")
                summary_parts.append(f"     Action: {action_preview}")
                # 不再显示 observation_preview 以节省 token
                summary_parts.append("")  # 步骤间空行

        summary_parts.append("")  # 工作流间空行分隔

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

    # 收集所有分支
    all_branches = []
    all_init_cmds = []

    for chunk in knowledge_chunks:
        all_init_cmds.extend(chunk.init_cmds)
        all_branches.extend(chunk.matrix)

    # 去重 init_cmds
    unique_init_cmds = []
    for cmd in all_init_cmds:
        if cmd not in unique_init_cmds:
            unique_init_cmds.append(cmd)

    # 更智能的 matrix 合并：基于 action 去重，避免重复的 action
    merged_branches = []

    for branch in all_branches:
        # 首先检查是否有完全相同的 action
        duplicate_found = False
        for existing in merged_branches:
            # 如果 action 完全相同，则认为是重复分支
            if branch.action == existing.action:
                duplicate_found = True
                logger.info(f"发现重复的 action，合并分支：{branch.action}")
                # 合并时保留更详细的信息
                if len(branch.trigger) > len(existing.trigger):
                    existing.trigger = branch.trigger
                if branch.arg_hints and not existing.arg_hints:
                    existing.arg_hints = branch.arg_hints
                if len(branch.why) > len(existing.why):
                    existing.why = branch.why
                if len(branch.expect) > len(existing.expect):
                    existing.expect = branch.expect
                break

        if not duplicate_found:
            # 如果没有完全相同的 action，检查是否有高度相似的 action 和 why
            is_similar = False
            for existing in merged_branches:
                # 相似性判断：如果 action 和 why 非常相似，则认为是同一个诊断步骤
                action_similarity = _calculate_similarity(
                    branch.action, existing.action
                )
                why_similarity = _calculate_similarity(branch.why, existing.why)

                # 如果 action 相似度很高（>0.9）且 why 相似度也较高（>0.7），则合并
                if action_similarity > 0.9 and why_similarity > 0.7:
                    is_similar = True
                    logger.info(
                        f"发现高度相似的 action，合并分支：{branch.action} (相似度：{action_similarity:.2f})"
                    )
                    # 可以选择保留更具体的那个
                    if len(branch.trigger) > len(existing.trigger):
                        existing.trigger = branch.trigger
                    if branch.arg_hints and not existing.arg_hints:
                        existing.arg_hints = branch.arg_hints
                    if len(branch.why) > len(existing.why):
                        existing.why = branch.why
                    if len(branch.expect) > len(existing.expect):
                        existing.expect = branch.expect
                    break

            if not is_similar:
                merged_branches.append(branch)

    # 特殊处理：过滤掉与 init_cmds 高度相似的诊断分支
    # 因为 init_cmds 已经是初始命令，不应该再作为单独的诊断分支出现
    filtered_branches = []
    for branch in merged_branches:
        # 检查 branch.action 是否与任何 init_cmd 高度相似
        is_init_cmd_variant = False
        for init_cmd in unique_init_cmds:
            # 计算相似度，考虑命令前缀相同的情况
            if _is_command_variant(branch.action, init_cmd):
                is_init_cmd_variant = True
                logger.info(
                    f"过滤掉与 init_cmd 相似的诊断分支：{branch.action} (类似 {init_cmd})"
                )
                break

        if not is_init_cmd_variant:
            filtered_branches.append(branch)
        else:
            # 如果是 init_cmd 变体，检查是否需要更新 init_cmd
            # 例如，如果分支有更具体的参数，可以更新 init_cmd
            for i, init_cmd in enumerate(unique_init_cmds):
                if _is_command_variant(branch.action, init_cmd) and len(
                    branch.action
                ) > len(init_cmd):
                    # 使用更具体的命令版本
                    unique_init_cmds[i] = branch.action
                    logger.info(
                        f"更新 init_cmd 为更具体的版本：{init_cmd} -> {branch.action}"
                    )

    # 限制 init_cmds 数量为 2-3（根据提示词要求 2-3，但代码中其他地方可能有限制）
    if len(unique_init_cmds) > 3:
        # 保留前 3 个
        unique_init_cmds = unique_init_cmds[:3]
    elif len(unique_init_cmds) < 3:
        # 如果不足 3 个，添加一些通用命令
        default_cmds = [
            "sys -i",
            "bt -a",
            "log | grep -i lockup",
        ]
        for cmd in default_cmds:
            if cmd not in unique_init_cmds and len(unique_init_cmds) < 3:
                unique_init_cmds.append(cmd)

    # 最终检查：确保没有重复的 action
    final_branches = []
    seen_actions = set()
    for branch in filtered_branches:
        if branch.action not in seen_actions:
            seen_actions.add(branch.action)
            final_branches.append(branch)
        else:
            logger.warning(f"最终检查中发现重复的 action，已过滤：{branch.action}")

    logger.info(
        f"合并去重后：{len(final_branches)} 个唯一诊断分支（原始：{len(all_branches)} 个）"
    )

    return DiagnosticKnowledge(
        summary="Comprehensive diagnostic matrix for Linux kernel Hard LOCKUP scenarios",
        init_cmds=unique_init_cmds,
        matrix=final_branches,
    )


def _calculate_similarity(str1: str, str2: str) -> float:
    """
    计算两个字符串的简单相似度。

    Args:
        str1: 第一个字符串
        str2: 第二个字符串

    Returns:
        相似度分数 (0.0-1.0)
    """
    if not str1 or not str2:
        return 0.0

    # 转换为小写并分词
    words1 = set(str1.lower().split())
    words2 = set(str2.lower().split())

    if not words1 or not words2:
        return 0.0

    # 计算 Jaccard 相似度
    intersection = len(words1.intersection(words2))
    union = len(words1.union(words2))

    return intersection / union if union > 0 else 0.0


def _is_command_variant(cmd1: str, cmd2: str) -> bool:
    """
    检查两个命令是否是变体关系（例如 "sys" 和 "sys -i"）。

    Args:
        cmd1: 第一个命令
        cmd2: 第二个命令

    Returns:
        如果是命令变体则返回 True
    """
    if not cmd1 or not cmd2:
        return False

    # 去除前后空格
    cmd1 = cmd1.strip()
    cmd2 = cmd2.strip()

    # 如果完全相同
    if cmd1 == cmd2:
        return True

    # 获取命令的基本部分（第一个单词）
    cmd1_base = cmd1.split()[0] if cmd1 else ""
    cmd2_base = cmd2.split()[0] if cmd2 else ""

    # 如果基本命令不同，则不是变体
    if cmd1_base != cmd2_base:
        return False

    # 检查是否一个命令是另一个命令的前缀
    # 例如 "sys" 是 "sys -i" 的前缀
    if cmd1.startswith(cmd2) or cmd2.startswith(cmd1):
        return True

    # 计算相似度
    similarity = _calculate_similarity(cmd1, cmd2)
    return similarity > 0.7


def validate_diagnostic_knowledge(
    knowledge: DiagnosticKnowledge, total_workflow_steps: int = 0
) -> Dict[str, Any]:
    """
    验证诊断知识库的质量。

    Args:
        knowledge: 诊断知识库
        total_workflow_steps: 所有 DSL 文件中的总工作流步骤数，用于计算覆盖率

    Returns:
        验证结果
    """
    issues = []
    warnings = []

    # 检查 init_cmds 数量（根据提示词调整为 2-3 个）
    init_cmd_count = len(knowledge.init_cmds)
    if init_cmd_count < 3:
        warnings.append(f"init_cmds 数量较少 ({init_cmd_count})，建议 2-3 个以覆盖全面")
    elif init_cmd_count > 8:
        warnings.append(f"init_cmds 数量过多 ({init_cmd_count})，建议 2-3 个")

    # 检查 matrix 数量
    matrix_count = len(knowledge.matrix)
    if matrix_count < 30:
        warnings.append(f"诊断分支数量较少 ({matrix_count})，建议更多以覆盖全面")
    elif matrix_count > 200:
        warnings.append(f"诊断分支数量过多 ({matrix_count})，可能存在冗余")

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

        # 检查系统命令（应该只使用 crash 工具命令）
        system_command_patterns = [
            r"\bcat\b",
            r"\bps\b",
            r"\biostat\b",
            r"\bifconfig\b",
            r"\bdf\b",
            r"\bmpstat\b",
            r"\bdmesg\b",
            r"\blsmod\b",
            r"\bmodinfo\b",
            r"\blspci\b",
            r"\bgrep\s+[^-]",
            r"\bawk\b",
            r"\bsed\b",
            r"\bwatch\b",
            r"\becho\b",
            r"\btail\b",
            r"\bhead\b",
            r"\bwc\b",
            r"\bsort\b",
            r"\buniq\b",
            r"\bpaste\b",
        ]

        for pattern in system_command_patterns:
            if re.search(pattern, branch.action, re.IGNORECASE):
                warnings.append(
                    f"分支 {i+1} action 中包含系统命令（应使用 crash 工具命令）：{branch.action[:50]}..."
                )
                break

    # 计算覆盖率（如果提供了总工作流步骤数）
    coverage_info = {}
    if total_workflow_steps > 0:
        coverage_ratio = (
            matrix_count / total_workflow_steps if total_workflow_steps > 0 else 0
        )
        coverage_percentage = coverage_ratio * 100

        # 调整覆盖率阈值：目标至少 30% 覆盖率（因为有些步骤可能合并）
        if coverage_ratio < 0.3:
            warnings.append(
                f"覆盖率较低：{matrix_count}/{total_workflow_steps} ({coverage_percentage:.1f}%)，建议增加诊断分支以提高覆盖率"
            )
        elif coverage_ratio > 1.5:
            warnings.append(
                f"覆盖率过高：{matrix_count}/{total_workflow_steps} ({coverage_percentage:.1f}%)，可能存在冗余分支"
            )

        coverage_info = {
            "total_workflow_steps": total_workflow_steps,
            "coverage_ratio": coverage_ratio,
            "coverage_percentage": coverage_percentage,
        }

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
        "coverage": coverage_info,
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
    # 估算提示词 token 数：简化后的提示词大约 500-800 tokens，加上 schema 大约 300-500 tokens
    estimated_prompt_tokens = 1200
    chunks = chunk_workflows(workflows_data, prompt_tokens=estimated_prompt_tokens)

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

    # 计算总工作流步骤数
    total_workflow_steps = sum(w.get("total_steps", 0) for w in workflows_data)

    # 验证结果（包含覆盖率计算）
    validation = validate_diagnostic_knowledge(merged_knowledge, total_workflow_steps)
    if validation["issues"]:
        logger.warning(f"验证发现问题：{validation['issues']}")
    if validation["warnings"]:
        logger.info(f"验证警告：{validation['warnings'][:3]}")  # 只显示前 3 个警告

    # 记录覆盖率信息
    if validation["coverage"]:
        coverage_info = validation["coverage"]
        logger.info(
            f"覆盖率统计：诊断分支数={len(merged_knowledge.matrix)}，工作流总步骤数={total_workflow_steps}，覆盖率={coverage_info['coverage_percentage']:.1f}%"
        )

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

        # 重新计算总工作流步骤数以显示覆盖率
        workflows_data = []
        for file_path in os.listdir(dsl_dir):
            if file_path.endswith(".json"):
                try:
                    with open(
                        os.path.join(dsl_dir, file_path), "r", encoding="utf-8"
                    ) as f:
                        content = f.read()
                    workflow_data = extract_workflow_data(content)
                    if "error" not in workflow_data:
                        workflows_data.append(workflow_data)
                except Exception:
                    pass

        total_workflow_steps = sum(w.get("total_steps", 0) for w in workflows_data)
        coverage_percentage = (
            (len(result.matrix) / total_workflow_steps * 100)
            if total_workflow_steps > 0
            else 0
        )

        # 显示统计信息
        print(f"\n{'='*60}")
        print("📊 诊断知识库统计")
        print(f"{'='*60}")
        print(f"总结：{result.summary}")
        print(f"初始命令数量：{len(result.init_cmds)} (目标：2-3 个)")
        print(f"诊断分支总数：{len(result.matrix)}")
        print(f"工作流总步骤数：{total_workflow_steps}")
        print(
            f"覆盖率：{len(result.matrix)}/{total_workflow_steps} ({coverage_percentage:.1f}%)"
        )

        # 分类统计
        diagnostic_branches = [b for b in result.matrix if not b.is_end]
        end_branches = [b for b in result.matrix if b.is_end]

        print(f"  诊断步骤：{len(diagnostic_branches)}")
        print(f"  结论分支：{len(end_branches)}")

        # 显示初始命令
        print(f"\n📋 初始诊断命令 ({len(result.init_cmds)} 个):")
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
