#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# prompts.py - VMCore 分析 Agent 提示词定义模块
# Author: CalmWU
# Created: 2026-01-09

from .prompt_layers import LAYER0_SYSTEM_PROMPT_TEMPLATE, PLAYBOOKS, SOP_FRAGMENTS
from .prompt_overlays import DRIVER_OBJECT_OVERLAY, STACK_CORRUPTION_OVERLAY
from .prompt_phrases import (
    CANARY_POINTER_VALUE_PARTIAL_DUMP_RULE,
    CANARY_POINTER_VALUE_RULE,
    STACK_CAUSALITY_RED_LINE_RULE,
)
from .schema import (
    get_corruption_mechanism_aliases,
    get_corruption_mechanism_values,
    get_partial_dump_values,
    get_root_cause_class_aliases,
    get_root_cause_class_values,
    get_signature_class_aliases,
    get_signature_class_values,
)


def _quote_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _invalid_aliases_text() -> str:
    aliases = sorted(
        {
            *get_signature_class_aliases().keys(),
            *get_root_cause_class_aliases().keys(),
            *get_corruption_mechanism_aliases().keys(),
        }
    )
    return _quote_values(tuple(aliases))


def _quote_alias_map(alias_map: dict[str, str]) -> str:
    items = [f"'{alias}' -> '{canonical}'" for alias, canonical in alias_map.items()]
    return ", ".join(items)


def build_minimal_schema_enum_contract() -> str:
    """构造与 schema 同步的最小结构化输出枚举约束。"""
    return (
        "Allowed enum values in final JSON:\n"
        f"- signature_class: {_quote_values(get_signature_class_values())}\n"
        f"- root_cause_class: {_quote_values(get_root_cause_class_values())}\n"
        f"- corruption_mechanism: {_quote_values(get_corruption_mechanism_values())}\n"
        f"- partial_dump: {_quote_values(get_partial_dump_values())}\n"
        "Do not emit aliases or shorthand in final JSON. Normalize them to canonical schema values first.\n"
        f"- signature_class aliases to normalize: {_quote_alias_map(get_signature_class_aliases())}\n"
        f"- root_cause_class aliases to normalize: {_quote_alias_map(get_root_cause_class_aliases())}\n"
        f"- corruption_mechanism aliases to normalize: {_quote_alias_map(get_corruption_mechanism_aliases())}"
    )


_ANALYSIS_PROMPT_COMPATIBILITY_APPENDIX = f"""
## Compatibility Appendix

### Minimal-Output Contract Reminder

- active_hypotheses and gates are executor-managed internal state and MUST NOT appear in your JSON.

### Type Validation Guardrails

Q4 — Offset coverage:
- Before interpreting a runtime object as a candidate type, verify that the candidate type's SIZE covers the largest offset already observed in disassembly for that same pointer.
- If disassembly has already accessed offsets beyond the candidate type's SIZE, the fact that debug info contains that type is not enough.
- In that case, you MUST reject that type immediately and continue validating the real runtime object shape instead of forcing interpretation through a too-small type.

### Address Arithmetic Discipline

- this agent forbids emitting address arithmetic directly in crash actions.
- This agent forbids emitting address arithmetic directly in crash actions.
- Never emit rd -x <addr>+<offset> <count>, rd -x <addr>-<offset> <count>, or any similar inline arithmetic as the final action.
- This prohibition applies to rd, struct, dis, ptov, vtop, search, kmem, and every other crash command that takes an address operand.
- Pre-compute the final literal address first, then issue rd, struct, or related commands against that literal target.
- Good example:
    reasoning: "ffff8b817de17a10 - 0x40 = ffff8b817de179d0"
    action: "rd -x ffff8b817de179d0 16"
- Bad example:
    action: "rd -x ffff8b817de17a10-0x40 16"
- Before finalizing any action, perform a self-check: if the action string still contains +, -, parentheses, $-substitution, or register syntax inside an address operand, the action is invalid and must be rewritten with a literal address.

### Stack-Corruption Mechanism Closure

- {CANARY_POINTER_VALUE_RULE}
- {CANARY_POINTER_VALUE_PARTIAL_DUMP_RULE}
- ⛔ CANARY INVARIANT: The stack protector prologue unconditionally writes the canary at function entry. Therefore "pre-fault residual-stack pollution" is NOT a valid canary corruption mechanism. Only writes occurring DURING the canary-bearing function's execution can corrupt the canary.
- Before finalizing, explicitly evaluate these mechanism families: self-frame local overflow (the canary-bearing function's own code or its unprotected leaf callees), exception-path local overwrite, and current/current->field spill or copy overflow.
- Prefer `resolve_stack_canary_slot` for canary-slot and frame-pointer-chain closure. Only if the tool is unavailable or unproven may you fall back to verified RBP arithmetic; never scan the stack for recognizable values and reverse-justify the address.
- Final diagnosis must either identify the most supported mechanism family or explicitly bound the remaining open set and explain why dump limitations prevent closure.
- A conclusion that jumps directly from "task pointer in canary slot" to a broad subsystem blame without mechanism analysis is incomplete.

### Exception-Boundary Provenance Guardrail

- For page fault, interrupt, NMI, and similar nested paths, do NOT treat the visible backtrace as a single ordinary caller/callee chain for stack-overflow causality.
- First separate: interrupted normal-path frames, hardware/pt_regs exception-entry state, and exception-handler frames.
- Relative frame addresses alone are insufficient to claim that a handler frame is the overflow source for a canary found in another exception-path frame, or that an interrupted pre-exception frame overflowed into a handler frame.
- If the suspected source and the corrupted slot are separated by an exception-entry boundary, local-overflow attribution remains provisional until frame provenance and active-overlap arithmetic are explicitly proven.
- When reasoning is not proven, keep multiple mechanisms open: active overwrite inside the exception path, stack-slot reuse from pre-fault returned frames, or frame reconstruction error.

### Review Red-Line Rule: Exception-Boundary Overflow Claims

- Reject any conclusion that blames handle_mm_fault or another exception-path frame for canary corruption, or blames an interrupted pre-fault frame for a handler-frame canary, when the only support is relative stack position or ordinary downward-stack reasoning across a page-fault, interrupt, NMI, or similar exception boundary.
- Such claims are invalid until the analysis explicitly proves frame provenance, exception-entry layout, and active overlap of the relevant stack regions.

### Review Red-Line Rule: Evidence-Free Suspect Promotion

- Reject any conclusion that names handle_mm_fault or any other function as the likely overflow source when the support is only a non-trivial stack allocation, a deep in-function offset, or vague statements such as "large stack frame" or "complex routine with substantial local state."
- A suspect function must be tied to the corrupted slot by concrete write evidence: an overflow-capable local object, a copy or store primitive, validated overlap arithmetic, or stack-byte provenance. Otherwise the result must remain provisional.

### Review Red-Line Rule: Stack-Resident Code Pointer Is Not Writer Proof

- Reject any conclusion that infers "function X caused the overflow" merely because an address inside function X appears on the stack.
- A kernel text address found on the stack is first evidence about the value that was written, copied, spilled, or left as residue. It is not yet evidence about which function performed the overwrite.
- Before using a stack-resident code pointer in root-cause attribution, the analysis must distinguish saved return site, copied function pointer, callback-table payload, stale stack residue, and dump artifact.

### Review Red-Line Rule: Active Call Chain First

- When the panic task remains on a coherent non-exception path, inspect that live chain before promoting exception handlers to suspects.
- Example: if the active path is sys_open -> do_filp_open -> path_openat -> do_last -> link_path_walk -> inode_permission, those VFS/open-path frames must be audited with disassembly and stack-layout reasoning before any blame shifts to handle_mm_fault or fault.c.
- A final recommendation that jumps directly from a stack-resident handle_mm_fault return site to arch/x86/mm/fault.c is incomplete unless the active syscall-path frames have already been checked and ruled down.

### Review Red-Line Rule: Current-Valued Canary Requires Spill Proof

- Reject any conclusion that explains a current-valued canary by naming a specific function's local overflow unless the analysis identifies the exact stack spill slot for current or a current-derived pointer and proves that a neighboring overflow-capable local object or write primitive could reach that slot.
- Mere access to current, generic task_struct usage, or an unspecified stack spill is not enough.

### Review Red-Line Rule: Causality-Eliminated Frames

- {STACK_CAUSALITY_RED_LINE_RULE}

### Review Red-Line Rule: Invalid Caller-Edge Narratives

- Reject any conclusion that narrates two adjacent corrupted-backtrace frames as a proven ordinary caller-callee edge when static code structure does not support that edge, or when the edge crosses unrelated subsystems without a proven exception bridge.
- Examples include treating a VFS permission helper as if it ordinarily called zone_statistics, or treating a scan-derived ? frame adjacency as a real call chain.
- In such cases the analysis must first downgrade bt reliability and choose among bounded explanations such as exception-path splice, stack-scan artifact, stale-frame residue, or corrupted saved return path. It must not invent a normal call edge, and it must not promote a specific RIP-jump theory without validating saved return addresses or frame provenance.

### No-Op Command Hygiene

- Do not use crash commands to print notes that are already present in reasoning or prior tool output.
- Never emit echo, printf, shell comments, separators, or breadcrumb text such as "Frame #4 address from bt is ..." inside action or run_script.
- run_script is for bundling multiple diagnostic commands, not for narration.
- Before finalizing an action, remove any command line that does not gather new evidence or change diagnostic state.

### Log Filtering Contract

- If you need to search kernel logs after initial analysis, the emitted action itself MUST literally contain `| grep`.
- NEVER emit `log -m`, `log -t`, or `log -a` standalone in the action field, and do not pipe them to `head`, `tail`, `sed`, or other commands before grep.

### Memory Sweep Contract

- If you use `rd -SS`, the action MUST include an explicit small count and a concrete grep anchor.
- NEVER emit large-range `rd -SS` sweeps paired with broad printable-character filters such as `grep -E '[ -~]{{8,}}'`, `[[:print:]]`, or equivalent "show me arbitrary ASCII" patterns.
- Prefer a narrow window plus a symbol name, device tag, validated string fragment, or other specific anchor that is already motivated by the evidence.
""".strip()


def _unique_prompt_sections(sections: list[str]) -> list[str]:
    """
    移除 prompt 章节列表中的重复项并进行标准化处理。

    该函数会去除每个章节字符串前后的空白字符，并确保返回的列表中
    不包含重复的章节内容。

    Args:
        sections: 原始的 prompt 章节字符串列表。

    Returns:
        去重且去除首尾空格后的章节字符串列表。
    """
    unique_sections: list[str] = []
    seen: set[str] = set()
    for section in sections:
        normalized = section.strip()
        # 跳过空字符串或已经出现过的章节
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_sections.append(normalized)
    return unique_sections


def _is_stack_protector_prompt_case(
    signature_class: str | None,
    recent_text: str,
) -> bool:
    """
    判断当前的分析情况是否属于“栈保护（Stack Protector）”检测触发的案例。

    通过检查签名类别是否为 'stack_corruption' 以及最近的分析文本中是否包含
    特定的栈保护相关关键字（如 stack-protector, __stack_chk_fail 等）来判定。

    Args:
        signature_class: 识别出的特征签名类别。
        recent_text: 最近的文本分析内容，用于关键字匹配。

    Returns:
        如果匹配到栈保护相关的特征，则返回 True；否则返回 False。
    """
    # 只有当特征类别明确为栈损坏时，才进一步检查关键字
    if signature_class != "stack_corruption":
        return False

    lowered_recent_text = recent_text.lower()
    # 定义识别栈保护机制的特征关键字
    return any(
        marker in lowered_recent_text
        for marker in (
            "stack-protector",
            "__stack_chk_fail",
            "kernel stack is corrupted in",
        )
    )


def _select_prompt_playbook(
    signature_class: str | None,
    recent_text: str,
) -> str:
    """
    根据识别出的签名类别（signature_class）和最近的文本上下文，
    从预定义的 Playbooks 库中选择最合适的提示词模板（Prompt Playbook）。

    该函数通过匹配特定的安全特征（如 Stack Protector Canary）
    或者直接使用识别到的签名类名来决定使用哪个分析策略。

    Args:
        signature_class (str | None): 识别出的漏洞签名类别名称。
                                     如果为 None 或 "unknown"，则不进行特定策略选择。
        recent_text (str): 最近的分析上下文文本，用于进行更细粒度的启发式匹配。

    Returns:
        str: 返回选定的 Prompt Playbook 模板内容。
             如果找不到匹配的模板或输入无效，则返回空字符串。
    """
    if not signature_class or signature_class == "unknown":
        return ""

    # 特殊情况处理：如果检测到栈保护（Stack Protector Canary）的相关特征，
    # 则优先使用专门针对 Canary 注入/绕过分析的 playbook。
    if _is_stack_protector_prompt_case(signature_class, recent_text):
        return PLAYBOOKS.get("stack_protector_canary", "")

    # 默认情况：根据签名类名直接从 PLAYBOOKS 字典中检索对应的模板
    return PLAYBOOKS.get(signature_class, "")


def _select_prompt_sop_fragments(
    *,
    signature_class: str | None,
    recent_text: str,
    root_cause_class: str | None,
    step_count: int,
    enabled_gates: set[str],
) -> list[str]:
    """
    根据当前的分析上下文（签名、最近文本、分析步骤、已启用的检查点等）
    动态选择并组合标准操作程序（SOP）的片段。

    该函数充当决策引擎，通过检查特定的特征（如关键字、错误类型、分析深度）
    来决定向用户或 Agent 推荐哪些后续分析步骤或诊断建议。

    Args:
        signature_token: 识别出的错误签名分类（如 'pointer_corruption', 'use_after_free' 等）。
        recent_text: 最近分析过程中的文本输出或日志内容，用于进行关键字匹配。
        root_cause_class: 识别出的根本原因分类。
        step_count: 当前分析已经进行的步骤计数，用于判断是否需要引入更高级或更深入的技巧。
        enabled_gates: 当前启用的功能开关或检查点集合，用于强制启用某些 SOP 片段。

    Returns:
        A list of strings, where each string is a key to a specific SOP fragment in SOP_FRAGMENTS.
    """
    fragments: list[str] = []
    lowered_recent_text = recent_text.lower()
    # 检查当前是否处于栈保护（stack protector）相关的分析场景
    stack_protector_case = _is_stack_protector_prompt_case(
        signature_class,
        recent_text,
    )

    # 1. DMA 损坏检测：如果启用了 DMA 检查，或者在指针/UAF 场景下且分析已深入，并发现了 DMA 相关特征
    if "dma_corruption" in enabled_gates or (
        signature_class in {"pointer_corruption", "use_after_free"}
        and step_count >= 10
        and (
            root_cause_class == "dma_corruption"
            or "dma" in lowered_recent_text
            or "iommu" in lowered_recent_text
        )
    ):
        fragments.append(SOP_FRAGMENTS["dma_corruption"])

    # 2. Per-CPU 访问检测：检查是否涉及 GS 寄存器或 Per-CPU 内存区域的访问
    if "per_cpu_access" in enabled_gates or any(
        token in lowered_recent_text for token in ("%gs", "per-cpu", "per_cpu", "gs:")
    ):
        fragments.append(SOP_FRAGMENTS["per_cpu_access"])

    # 3. 地址搜索建议：当检测到地址转换（ptov）或内存搜索相关指令时
    if "address_search" in enabled_gates or any(
        token in lowered_recent_text
        for token in ("search", "address", "ptov", "kmem -p")
    ):
        fragments.append(SOP_FRAGMENTS["address_search"])

    # 4. 驱动源码关联检测：在分析深入到一定程度时，尝试寻找驱动程序相关的符号或函数指针特征
    if "driver_source_correlation" in enabled_gates or (
        signature_class in {"pointer_corruption", "use_after_free"}
        and step_count >= 8
        and any(
            token in lowered_recent_text
            for token in (
                "function pointer",
                "_base_",
                "mod -s",
                "sym ",
                "apic",
                "fee0",
                "list_head",
                "self-referential",
                "self reference",
            )
        )
    ):
        fragments.append(SOP_FRAGMENTS["driver_source_correlation"])

    # 5. 栈溢出检测：排除掉已知的栈保护机制触发的情况，仅在检测到明显的栈损坏关键字时建议
    if "stack_overflow" in enabled_gates or (
        (
            "stack overflow" in lowered_recent_text
            or "stack corruption" in lowered_recent_text
        )
        and not stack_protector_case
    ):
        fragments.append(SOP_FRAGMENTS["stack_overflow"])

    # 6. 栈保护机制处理：如果匹配到栈保护特征，建议检查 fast path 逻辑；否则，如果是纯栈损坏，建议检查栈帧
    if "stack_protector_fast_path" in enabled_gates or stack_protector_case:
        fragments.append(SOP_FRAGMENTS["stack_protector_fast_path"])
    elif signature_class == "stack_corruption":
        fragments.append(SOP_FRAGMENTS["stack_frame_forensics"])

    # 7. KASAN/UBSAN 检测：检测日志中是否出现了内核运行时检测工具的输出
    if "kasan_ubsan" in enabled_gates or any(
        token in lowered_recent_text for token in ("kasan", "ubsan")
    ):
        fragments.append(SOP_FRAGMENTS["kasan_ubsan"])

    # 8. 高级技术引入：当分析步数非常多且处于严重的内存破坏场景时，引入更复杂的分析技术
    if "advanced_techniques" in enabled_gates or (
        step_count >= 18
        and signature_class
        in {"pointer_corruption", "use_after_free", "general_protection_fault"}
    ):
        fragments.append(SOP_FRAGMENTS["advanced_techniques"])

    return fragments


def _select_prompt_overlays(
    *,
    signature_class: str | None,
    recent_text: str,
    root_cause_class: str | None,
    step_count: int,
    enabled_gates: set[str],
) -> list[str]:
    """
    根据当前的分析状态、发现的签名以及分析步骤，决定是否需要在 Prompt 中添加额外的提示层（Overlays）。

    Overlays 用于在特定的分析上下文（如发现内存损坏、驱动相关线索或达到一定分析深度）时，
    为 LLM 提供额外的上下文信息或指导性指令，以引导其进行更深入或更具针对性的分析。

    Args:
        signature_class: 当前识别出的异常签名类别 (例如 "stack_corruption", "pointer_corruption" 等)。
        recent_text: 最近分析步骤生成的文本内容，用于关键词匹配。
        root_cause_class: 识别出的根本原因类别 (例如 "dma_corruption" 等)。
        step_count: 当前分析任务已进行的步骤总数。
        enabled_gates: 当前启用的分析闸门（Gates）集合，用于控制某些高级提示是否激活。

    Returns:
        list[str]: 需要添加的 Overlay 标识符列表。
    """
    overlays: list[str] = []
    lowered_recent_text = recent_text.lower()

    # 如果检测到栈损坏，添加栈损坏相关的提示层，引导模型关注栈帧和返回地址
    if signature_class == "stack_corruption":
        overlays.append(STACK_CORRUPTION_OVERLAY)

    # 决定是否添加驱动对象相关的提示层 (DRIVER_OBJECT_OVERLAY)
    # 条件：显式启用了该闸门，或者满足特定的内存损坏上下文及深度要求
    if "driver_object_overlay" in enabled_gates or (
        signature_class in {"pointer_corruption", "use_after_free"}
        and (
            # 情况 1: 已经确定是 DMA 相关的损坏
            root_cause_class == "dma_corruption"
            # 情况 2: 分析步数已足够多，可以考虑引入更复杂的驱动对象分析逻辑
            or step_count >= 8
            # 情况 3: 在最近的文本中发现了与驱动、模块或内核对象相关的敏感关键词
            or any(
                token in lowered_recent_text
                for token in (
                    "function pointer",
                    "_base_",
                    "mod -s",
                    "sym ",
                    "apic",
                    "fee0",
                    "list_head",
                    "self-referential",
                    "self reference",
                    "third-party",
                    "out-of-tree",
                )
            )
        )
    ):
        overlays.append(DRIVER_OBJECT_OVERLAY)

    return overlays


def analysis_crash_prompt(
    *,
    signature_class: str | None = None,
    recent_text: str = "",
    root_cause_class: str | None = None,
    step_count: int = 0,
    enabled_gates: set[str] | None = None,
) -> str:
    """
    构建用于崩溃分析（Crash Analysis）的完整 Prompt 字符串。

    该函数通过组合多个层级的 Prompt 片段（System Prompt, Playbook, Overlays, SOP Fragments 等）
    来构建一个结构化的、上下文丰富的指令，引导 LLM 进行深入的内核崩溃根因分析。

    Args:
        signature_class: 崩溃特征类（Signature Class），用于匹配特定的分析策略（Playbook）。
        recent_text: 最近的分析日志或上下文文本，用于提供当前的分析状态。
        root_cause_class: 已识别的根因类，用于调整分析的侧重点。
        step_count: 当前分析步骤的计数，用于在 Prompt 中体现分析进度。
        enabled_gates: 当前启用的“门控”（Gates）集合，用于控制 Prompt 中包含哪些特定的验证或约束逻辑。

    Returns:
        str: 拼接完成的、用于发送给 LLM 的完整 Prompt 文本。
    """
    # 预处理 enabled_gates：去除空白字符、转换为小写，并过滤掉空字符串，确保匹配的一致性
    gates = {gate.strip().lower() for gate in (enabled_gates or set()) if gate.strip()}

    # 构建 Prompt 的各个组成部分
    sections = _unique_prompt_sections(
        [
            # 基础层：最核心的系统级指令（System Prompt）
            LAYER0_SYSTEM_PROMPT_TEMPLATE,
            # 策略层：根据崩溃特征和上下文选择对应的分析剧本（Playbook）
            _select_prompt_playbook(signature_class, recent_text),
            # 叠加层：根据当前分析状态（根因、步骤、门控）动态添加的上下文约束或补充说明
            *_select_prompt_overlays(
                signature_class=signature_class,
                recent_text=recent_text,
                root_cause_class=root_cause_class,
                step_count=step_count,
                enabled_gates=gates,
            ),
            # 标准作业程序（SOP）片段：注入具体的分析步骤或操作指南
            *_select_prompt_sop_fragments(
                signature_class=signature_class,
                recent_text=recent_text,
                root_cause_class=root_cause_class,
                step_count=step_count,
                enabled_gates=gates,
            ),
            # 附录层：关于分析兼容性、输出格式等底层的补充信息
            _ANALYSIS_PROMPT_COMPATIBILITY_APPENDIX,
        ]
    )

    # 使用两个换行符将所有非重复的 Prompt 片段连接成一个完整的文本块
    return "\n\n".join(sections)


def crash_init_data_prompt() -> str:
    return """
# Initial Context
The following is the User-Provided Initial Context for this Linux kernel crash analysis. It includes the items listed in the User-Provided Data Inventory below and should be treated as already-available analysis input.

**CRITICAL**: These information blocks and command outputs are already provided below by the user. **DO NOT** attempt to request this data or run these base commands (`sys`, `sys -t`, `bt`) again at ANY step of the analysis.

**[User-Provided Data Inventory]**
1. **`sys`**: System info (kernel version, panic string, CPU count).
2. **`sys -t`**: Kernel taint flags.
3. **`bt`**: Panic task backtrace.
4. **`vmcore-dmesg`**: **IMPORTANT** - This is a text content block embedded in the User-Provided Initial Context below, NOT a file in the crash utility environment. You CANNOT run shell commands like `grep -i pattern vmcore-dmesg` on it. Instead, analyze the text directly from the User-Provided Initial Context.
5. **Third-party Modules**: Paths to installed modules with debug symbols.

**[Instructions for Initial Analysis]**
- **Evaluation**: Pay special attention to `BUG:`,`Oops`,`panic`,`MCE` entries within the `vmcore-dmesg` content block. These are critical kernel error signals.
- **`sys -t` Triage Role**: Treat `sys -t` as one of the first environment-classification signals. Its main value is fast triage: it helps judge whether the crash happened in a clean kernel environment or in a kernel already marked by warnings, machine checks, or third-party module involvement. Use it to rank hypotheses and decide what evidence to prioritize next.
- **Clean vs Tainted Interpretation**: `TAINTED_MASK: 0` means no taint flags are set. This removes taint-based support barriers and keeps in-tree kernel code, workload-triggered behavior, firmware issues, and hardware faults all in scope. Do **NOT** overstate taint-free output as proof that the root cause must be a pure upstream kernel bug.
- **Third-Party Module Signal**: Flags such as `P`, `O`, and `E` indicate proprietary, externally built, or unsigned modules. Treat these as a strong cue to inspect third-party modules early, especially when the backtrace crosses those modules or the failing subsystem is tightly adjacent to them. This changes supportability and hypothesis ranking, but it is still not proof unless the crash path or other diagnostic evidence points there.
- **Warning and Hardware Signal**: `W` means the kernel recorded a warning before or during the failure sequence; check whether that warning is the trigger, an earlier symptom, or unrelated noise by correlating it with the `vmcore-dmesg` timeline and the panic path. `M` elevates hardware-error or machine-check validation and should trigger explicit hardware-oriented checks rather than immediate software-only blame.
- **Reliability Caveat**: Taint flags affect how to interpret later evidence. Out-of-tree or private modules may limit symbol visibility and debuginfo quality. A prior warning may mean the fatal crash is downstream from earlier damage. Do not map taint letters mechanically to a crash type, and do not infer deadlock, ownership, or temporal causality from taint flags alone.
- **Follow-up Direction**: Always interpret `sys -t` together with `bt`, `vmcore-dmesg`, and the module inventory. If taint suggests warning history, inspect the warning context in the provided `vmcore-dmesg` first. If taint suggests third-party module involvement, compare the backtrace against the loaded-module set before deep-diving into generic kernel hypotheses.
- **Example Workflow (`W`)**:
  1. `sys -t` shows `W` -> first inspect `vmcore-dmesg` for the warning site and timeline, not just the final panic line.
  2. Compare the warning location with `bt`; if the panic path stays in the same subsystem, raise that warning as a leading trigger hypothesis.
  3. If the warning is much earlier or from a different subsystem, treat it as possible precursor damage and keep causal linkage provisional.
- **Example Workflow (`P/O/E`)**:
  1. `sys -t` shows `P`, `O`, or `E` -> first compare `bt` against the loaded third-party module set and note whether the call path crosses those modules.
  2. If the crash path enters a third-party module or directly adjacent callback path, promote that module family in the hypothesis ranking and account for symbol/debug-info limitations.
  3. If no third-party module appears on the active path, keep them as environmental risk factors rather than the default root cause.
- **Integration**: You MUST integrate your reasoning over the critical kernel error alongside the `bt` (backtrace) evaluation. Do not analyze them in isolation.
- **Log Searching**: If you need to search for specific patterns in the kernel log AFTER initial analysis, the emitted action itself MUST literally contain `| grep`, and any action containing a pipeline must be encoded as `{{"command_name": "run_script", "arguments": ["..."]}}`. Example: `log -m | grep -i nouveau | grep -Ei "fail|error|timeout|fault|xid|mmu|fifo"`. **NEVER emit `log -m`, `log -t`, or `log -a` standalone in the action field**, and do not pipe them to `head`, `tail`, `sed`, or other commands before grep. These forms dump the entire log, cause token overflow, and are invalid even if your reasoning mentions a filtered query. Do NOT attempt to use `grep` on vmcore-dmesg.

<initial_data>
{init_info}
</initial_data>
"""


def simplified_structure_reasoning_prompt() -> str:
    """
    简化版结构化推理提示词，仅要求模型输出核心字段，降低输出负担。
    复杂字段（如 gates、active_hypotheses）将在后处理阶段自动补齐。
    """
    signature_values = get_signature_class_values()
    root_cause_values = get_root_cause_class_values()
    mechanism_values = get_corruption_mechanism_values()
    partial_dump_values = get_partial_dump_values()
    invalid_aliases = _invalid_aliases_text()

    return (
        "You are a helper that extracts CORE information from unstructured vmcore crash analysis reasoning "
        "into a minimal structured JSON format.\n\n"
        "The analysis reasoning text will be provided in the next user message. Extract ONLY the following core fields from that text:\n\n"
        "Current analysis step number: {current_step}\n\n"
        "{force_conclusion}" + build_minimal_schema_enum_contract() + "\n\n"
        "REQUIRED FIELDS TO EXTRACT:\n"
        "1. 'reasoning': Summarize the key reasoning points (3-6 sentences)\n"
        "2. 'step_id': Set to {current_step}\n"
        "3. 'action': If the reasoning suggests a specific MCP tool call, return an object with exactly two fields: 'command_name' and 'arguments'. "
        'Example: {{"command_name": "rd", "arguments": ["-x", "ffff...", "16"]}}, {{"command_name": "run_script", "arguments": ["log -m | grep -i \\"mpt3sas\\" | grep -Ei \\"error|timeout|reset\\""]}}, or {{"command_name": "resolve_stack_canary_slot", "arguments": ["search_module_extables"]}}. Otherwise set it to null. Do NOT return action as a string.\n'
        "4. 'is_conclusive': Set to true ONLY if the reasoning explicitly states a final conclusion with root cause. "
        "Otherwise set to false.\n"
        f"5. 'signature_class': Extract the crash signature class from panic string analysis. Allowed values: {_quote_values(signature_values)}.\n"
        "6. 'root_cause_class': Extract the underlying root cause if the reasoning narrows it. Use null when it is not stated yet. "
        f"Allowed values: {_quote_values(root_cause_values)}.\n"
        "7. 'corruption_mechanism': Extract a finer-grained mechanism only when the reasoning supports it. "
        f"Allowed values: {_quote_values(mechanism_values)}. If absent or unsupported, set to null.\n"
        f"8. 'partial_dump': Use only these values: {_quote_values(partial_dump_values)}. If dump completeness is not explicitly mentioned, use 'unknown'.\n\n"
        "FIELDS TO SKIP (will be auto-filled later):\n"
        "- active_hypotheses\n"
        "- gates\n"
        "- final_diagnosis\n"
        "- fix_suggestion\n"
        "- confidence\n"
        "- additional_notes\n\n"
        "RULES:\n"
        "- Focus ONLY on extracting the required fields above\n"
        "- Keep reasoning concise and focused on what was learned from tool output\n"
        "- The schema definition below is the source of truth for field names and enum values. Follow it exactly even if the reasoning uses synonyms or old labels\n"
        "- Do not emit aliases or near-miss labels in final JSON. Invalid examples include "
        f"{invalid_aliases}. Convert them to the canonical values allowed by the schema\n"
        "- For root_cause_class, use 'stack_corruption' when stack damage is confirmed but the deeper mechanism is not yet proven. Use 'unknown' only when the reasoning bounds the failure family but still cannot isolate a canonical root-cause value\n"
        "- corruption_mechanism is narrower than root_cause_class. Put labels like 'field_type_misuse' or "
        "'missing_conversion' there, NEVER in root_cause_class\n"
        "- If labels like 'field_type_misuse', 'missing_conversion', 'write_corruption', or 'reinit_path_bug' appear "
        "in root_cause_class, that is a schema error and must be corrected before you answer\n"
        "- Any action containing a pipeline character '|' MUST use command_name='run_script' and store the full command line as a single string in arguments\n"
        "- DO NOT attempt to reconstruct complex hypothesis lists or gate statuses\n"
        "- Output MUST be valid JSON with ONLY the required fields above\n\n"
        "Schema for required fields only:\n"
        "```json\n"
        "{{\n"
        '  "step_id": {current_step},\n'
        '  "reasoning": "<3-6 sentence summary>",\n'
        '  "action": null,\n'
        '  "is_conclusive": false,\n'
        '  "signature_class": "null_deref",\n'
        '  "root_cause_class": "unknown",\n'
        '  "corruption_mechanism": null,\n'
        '  "partial_dump": "unknown"\n'
        "}}\n"
        "```\n\n"
        "If a follow-up tool call is needed, replace action=null with a complete command object such as "
        '{{"command_name": "dis", "arguments": ["-rl", "ffffffff81000000"]}}, {{"command_name": "run_script", "arguments": ["log -m | grep -i \\"nouveau\\" | grep -Ei \\"fail|error|timeout|fault|xid|mmu|fifo\\""]}}, or {{"command_name": "resolve_stack_canary_slot", "arguments": ["search_module_extables"]}}.\n\n'
        "REMEMBER: Skip complex fields! They will be handled automatically after your response.\n"
    )
