#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# prompts.py - VMCore 分析 Agent 提示词定义模块
# Author: CalmWU
# Created: 2026-01-09

from .prompt_layers import LAYER0_SYSTEM_PROMPT_TEMPLATE, PLAYBOOKS, SOP_FRAGMENTS

_ANALYSIS_PROMPT_COMPATIBILITY_APPENDIX = """
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
- Never emit rd -x <addr>+<offset> <count> or similar inline arithmetic as the final action.
- Pre-compute the final literal address first, then issue rd, struct, or related commands against that literal target.
""".strip()


def _unique_prompt_sections(sections: list[str]) -> list[str]:
    unique_sections: list[str] = []
    seen: set[str] = set()
    for section in sections:
        normalized = section.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_sections.append(normalized)
    return unique_sections


def analysis_crash_prompt() -> str:
    sections = _unique_prompt_sections(
        [
            LAYER0_SYSTEM_PROMPT_TEMPLATE,
            *PLAYBOOKS.values(),
            *SOP_FRAGMENTS.values(),
            _ANALYSIS_PROMPT_COMPATIBILITY_APPENDIX,
        ]
    )
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
- **Log Searching**: If you need to search for specific patterns in the kernel log AFTER initial analysis, you MUST pipe the log command with grep, and for noisy subsystems you MUST narrow the query with an additional grep stage or a specific error regex. Example: `log -m | grep -i nouveau | grep -Ei "fail|error|timeout|fault|xid|mmu|fifo"`. **NEVER use `log -m`, `log -t`, or `log -a` standalone** — they dump the entire log and cause token overflow. Do NOT attempt to use `grep` on vmcore-dmesg.

<initial_data>
{init_info}
</initial_data>
"""


def simplified_structure_reasoning_prompt() -> str:
    """
    简化版结构化推理提示词，仅要求模型输出核心字段，降低输出负担。
    复杂字段（如 gates、active_hypotheses）将在后处理阶段自动补齐。
    """
    return (
        "You are a helper that extracts CORE information from unstructured vmcore crash analysis reasoning "
        "into a minimal structured JSON format.\n\n"
        "The analysis reasoning text will be provided in the next user message. Extract ONLY the following core fields from that text:\n\n"
        "Current analysis step number: {current_step}\n\n"
        "{force_conclusion}"
        "REQUIRED FIELDS TO EXTRACT:\n"
        "1. 'reasoning': Summarize the key reasoning points (3-6 sentences)\n"
        "2. 'step_id': Set to {current_step}\n"
        "3. 'action': If the reasoning suggests a specific crash command, provide the COMPLETE command with all arguments. "
        "Otherwise set to null.\n"
        "4. 'is_conclusive': Set to true ONLY if the reasoning explicitly states a final conclusion with root cause. "
        "Otherwise set to false.\n"
        "5. 'signature_class': Extract the crash signature class from panic string analysis (e.g., 'null_deref', "
        "'use_after_free', 'pointer_corruption', etc.)\n"
        "6. 'root_cause_class': Extract the underlying root cause if mentioned (can be null during exploration)\n"
        "7. 'corruption_mechanism': Extract a finer-grained mechanism only when the reasoning supports it "
        "(e.g., 'field_type_misuse', 'missing_conversion', 'write_corruption', 'reinit_path_bug'). "
        "If absent or unsupported, set to null.\n"
        "8. 'partial_dump': Set this only if dump completeness is explicitly mentioned; otherwise use 'unknown'\n\n"
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
        "- For root_cause_class, use values like 'null_deref', 'use_after_free', 'wild_pointer', 'memory_corruption', "
        "'dma_corruption', etc. If uncertain, use 'unknown'\n"
        "- corruption_mechanism is narrower than root_cause_class. Put labels like 'field_type_misuse' or "
        "'missing_conversion' there, NEVER in root_cause_class\n"
        "- If labels like 'field_type_misuse', 'missing_conversion', 'write_corruption', or 'reinit_path_bug' appear "
        "in root_cause_class, that is a schema error and must be corrected before you answer\n"
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
        "If a follow-up command is needed, replace action=null with a complete command object.\n\n"
        "REMEMBER: Skip complex fields! They will be handled automatically after your response.\n"
    )
