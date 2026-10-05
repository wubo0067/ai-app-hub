#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# prompts.py - VMCore 分析 Agent 提示词定义模块
# Author: CalmWU
# Created: 2026-01-09

from .schema import (
    get_confidence_aliases,
    get_confidence_values,
    get_corruption_mechanism_aliases,
    get_corruption_mechanism_values,
    get_driver_inference_method_aliases,
    get_driver_inference_method_values,
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
            *get_driver_inference_method_aliases().keys(),
            *get_confidence_aliases().keys(),
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
        f"- driver_source_evidence.inference_method: {_quote_values(get_driver_inference_method_values())}\n"
        f"- confidence: {_quote_values(get_confidence_values())}\n"
        f"- partial_dump: {_quote_values(get_partial_dump_values())}\n"
        "Do not emit aliases, descriptive prose, or shorthand in final JSON. Normalize them to canonical schema values first.\n"
        f"- signature_class aliases to normalize: {_quote_alias_map(get_signature_class_aliases())}\n"
        f"- root_cause_class aliases to normalize: {_quote_alias_map(get_root_cause_class_aliases())}\n"
        f"- corruption_mechanism aliases to normalize: {_quote_alias_map(get_corruption_mechanism_aliases())}\n"
        f"- driver_source_evidence.inference_method aliases to normalize: {_quote_alias_map(get_driver_inference_method_aliases())}\n"
        f"- confidence aliases to normalize: {_quote_alias_map(get_confidence_aliases())}\n"
        "If driver_source_evidence is present and the method is uncertain, set inference_method to 'unknown'."
    )


def build_structure_reasoning_force_conclusion(*, is_last_step: bool) -> str:
    """构造 structured fallback 使用的最后一步附加约束。"""
    if not is_last_step:
        return ""

    return (
        "IMPORTANT: This is the LAST STEP. Do not request tools; action must be null. "
        "Set is_conclusive=true ONLY if the reasoning explicitly satisfies the Layer0 "
        "convergence criteria and contains a supported final diagnosis. "
        "If mandatory verification gaps remain, set is_conclusive=false and summarize "
        "the bounded non-conclusive findings in reasoning.\n\n"
    )


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
- **Mandatory Third-Party Module Symbol Loading**: When the inventory includes any third-party module path with debug symbols (i.e. the `debug_symbol_paths` list is non-empty), the first action of any tool call that will touch module-private symbols MUST be a `run_script` whose leading lines load EVERY listed ko via `mod -s <module> <path>` (one `mod -s` line per ko, in the listed order), followed by the dependent diagnostic commands in the same crash session. This applies to `dis -l`, `dis -s`, `sym`, `sym -m`, `struct`, `p`, and similar symbol-dependent commands. Never issue those commands as standalone single-tool actions against a third-party module before all `mod -s` lines. Note: `sym -l` is forbidden in any form; to enumerate a module's symbols use `sym -m <module>` or `sym -m <module> | grep -i <keyword>`.
- **Mandatory Source-Level Deepening After Symbol Resolution**: If `mod -s` succeeds and `dis -l` or `dis -s` resolves a third-party module function to concrete `file:line` source locations, do NOT stop at naming the function or high-level mechanism. Continue with source-level closure: inspect the exact branch, BUG/WARN site, loop, or sleep path in source; identify which state variables, struct fields, arguments, or task flags make that path fire; and validate those values from vmcore with concrete follow-up commands such as `task -R`, `struct`, `rd`, `bt <pid>`, or targeted disassembly around the source-mapped block.
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
        "- By schema definition, corruption_mechanism='reinit_path_bug' implies root_cause_class='race_condition'. Use that pairing explicitly when the reasoning supports a reinit-path bug\n"
        "- If labels like 'field_type_misuse', 'missing_conversion', 'write_corruption', or 'reinit_path_bug' appear "
        "in root_cause_class, that is a schema error and must be corrected before you answer\n"
        "- Any action containing a pipeline character '|' MUST use command_name='run_script' and store the full command line as a single string in arguments\n"
        "- For struct actions, use ONLY one of these forms: 'struct -o <type>' or 'struct <type> <addr>'. Never append field names such as 'driver' or 'init_name' after the address; compute a concrete field address separately if needed\n"
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
