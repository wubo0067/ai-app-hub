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
    unique_sections: list[str] = []
    seen: set[str] = set()
    for section in sections:
        normalized = section.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_sections.append(normalized)
    return unique_sections


def _is_stack_protector_prompt_case(
    signature_class: str | None,
    recent_text: str,
) -> bool:
    if signature_class != "stack_corruption":
        return False

    lowered_recent_text = recent_text.lower()
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
    if not signature_class or signature_class == "unknown":
        return ""

    if _is_stack_protector_prompt_case(signature_class, recent_text):
        return PLAYBOOKS.get("stack_protector_canary", "")

    return PLAYBOOKS.get(signature_class, "")


def _select_prompt_sop_fragments(
    *,
    signature_class: str | None,
    recent_text: str,
    root_cause_class: str | None,
    step_count: int,
    enabled_gates: set[str],
) -> list[str]:
    fragments: list[str] = []
    lowered_recent_text = recent_text.lower()
    stack_protector_case = _is_stack_protector_prompt_case(
        signature_class,
        recent_text,
    )

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

    if "per_cpu_access" in enabled_gates or any(
        token in lowered_recent_text for token in ("%gs", "per-cpu", "per_cpu", "gs:")
    ):
        fragments.append(SOP_FRAGMENTS["per_cpu_access"])

    if "address_search" in enabled_gates or any(
        token in lowered_recent_text
        for token in ("search", "address", "ptov", "kmem -p")
    ):
        fragments.append(SOP_FRAGMENTS["address_search"])

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

    if "stack_overflow" in enabled_gates or (
        (
            "stack overflow" in lowered_recent_text
            or "stack corruption" in lowered_recent_text
        )
        and not stack_protector_case
    ):
        fragments.append(SOP_FRAGMENTS["stack_overflow"])

    if "stack_protector_fast_path" in enabled_gates or stack_protector_case:
        fragments.append(SOP_FRAGMENTS["stack_protector_fast_path"])
    elif signature_class == "stack_corruption":
        fragments.append(SOP_FRAGMENTS["stack_frame_forensics"])

    if "kasan_ubsan" in enabled_gates or any(
        token in lowered_recent_text for token in ("kasan", "ubsan")
    ):
        fragments.append(SOP_FRAGMENTS["kasan_ubsan"])

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
    overlays: list[str] = []
    lowered_recent_text = recent_text.lower()

    if signature_class == "stack_corruption":
        overlays.append(STACK_CORRUPTION_OVERLAY)

    if "driver_object_overlay" in enabled_gates or (
        signature_class in {"pointer_corruption", "use_after_free"}
        and (
            root_cause_class == "dma_corruption"
            or step_count >= 8
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
    gates = {gate.strip().lower() for gate in (enabled_gates or set()) if gate.strip()}

    sections = _unique_prompt_sections(
        [
            LAYER0_SYSTEM_PROMPT_TEMPLATE,
            _select_prompt_playbook(signature_class, recent_text),
            *_select_prompt_overlays(
                signature_class=signature_class,
                recent_text=recent_text,
                root_cause_class=root_cause_class,
                step_count=step_count,
                enabled_gates=gates,
            ),
            *_select_prompt_sop_fragments(
                signature_class=signature_class,
                recent_text=recent_text,
                root_cause_class=root_cause_class,
                step_count=step_count,
                enabled_gates=gates,
            ),
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
- **Log Searching**: If you need to search for specific patterns in the kernel log AFTER initial analysis, the emitted action itself MUST literally contain `| grep`. Example: `log -m | grep -i nouveau | grep -Ei "fail|error|timeout|fault|xid|mmu|fifo"`. **NEVER emit `log -m`, `log -t`, or `log -a` standalone in the action field**, and do not pipe them to `head`, `tail`, `sed`, or other commands before grep. These forms dump the entire log, cause token overflow, and are invalid even if your reasoning mentions a filtered query. Do NOT attempt to use `grep` on vmcore-dmesg.

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
        'Example: {{"command_name": "rd", "arguments": ["-x", "ffff...", "16"]}} or {{"command_name": "resolve_stack_canary_slot", "arguments": ["search_module_extables"]}}. Otherwise set it to null. Do NOT return action as a string.\n'
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
        '{{"command_name": "dis", "arguments": ["-rl", "ffffffff81000000"]}} or {{"command_name": "resolve_stack_canary_slot", "arguments": ["search_module_extables"]}}.\n\n'
        "REMEMBER: Skip complex fields! They will be handled automatically after your response.\n"
    )
