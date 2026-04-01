#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from typing import Iterable, Optional, Sequence, cast

from langchain_core.messages import AIMessage, BaseMessage

from .graph_state import AgentState
from .prompt_layers import LAYER0_SYSTEM_PROMPT_TEMPLATE, PLAYBOOKS, SOP_FRAGMENTS
from .schema import CrashSignatureClass, GateEntry, Hypothesis, VMCoreLLMAnalysisStep


def build_analysis_system_prompt(state: AgentState, *, is_last_step: bool) -> str:
    prompt_parts = [
        LAYER0_SYSTEM_PROMPT_TEMPLATE.format(
            VMCoreAnalysisStep_Schema=json.dumps(
                VMCoreLLMAnalysisStep.model_json_schema(),
                indent=2,
            ),
        ),
        build_executor_state_section(state),
    ]

    playbook = _select_playbook(state.get("current_signature_class"))
    if playbook:
        prompt_parts.append(playbook)

    sop_fragments = _select_sop_fragments(state)
    if sop_fragments:
        prompt_parts.extend(sop_fragments)

    prompt_parts.append(
        "[STRUCTURED OUTPUT CONTRACT]\n"
        "active_hypotheses and gates are executor-managed. "
        "Omit them entirely from your JSON and return only the minimal schema fields."
    )

    if is_last_step:
        prompt_parts.append(
            "[CRITICAL WARNING]\n"
            "This is your LAST STEP. You have reached the execution limit.\n"
            "You MUST provide a final_diagnosis based on the information gathered so far.\n"
            "Set is_conclusive to true and do NOT request any further tool calls (action must be null)."
        )

    return "\n\n".join(part for part in prompt_parts if part)


def build_executor_state_section(state: AgentState) -> str:
    signature_class = state.get("current_signature_class") or "unknown"
    root_cause_class = state.get("current_root_cause_class") or "unknown"
    partial_dump = state.get("current_partial_dump") or "unknown"
    step_count = state.get("step_count", 0)

    hypotheses = _format_hypotheses(state.get("managed_active_hypotheses"))
    gates = _format_gates(state.get("managed_gates"))
    recent_commands = _recent_command_summaries(state.get("messages", []))
    stage_name = _infer_stage_name(step_count, state.get("managed_gates"))

    lines = [
        f"## Current Investigation State (Step {step_count})",
        f"- Signature class: {signature_class}",
        f"- Root cause class: {root_cause_class}",
        f"- Partial dump: {partial_dump}",
        f"- Current stage: {stage_name}",
        f"- Active hypotheses: {hypotheses}",
        f"- Gate status: {gates}",
        f"- Commands already run (do not repeat): {recent_commands}",
    ]
    return "\n".join(lines)


def _select_playbook(
    signature_class: Optional[CrashSignatureClass],
) -> str:
    if not signature_class or signature_class == "unknown":
        return ""
    return PLAYBOOKS.get(signature_class, "")


def _select_sop_fragments(state: AgentState) -> list[str]:
    fragments: list[str] = []
    signature_class = state.get("current_signature_class")
    root_cause_class = state.get("current_root_cause_class")
    step_count = state.get("step_count", 0)
    gates = cast(Optional[dict[str, GateEntry]], state.get("managed_gates")) or {}
    recent_text = _recent_text_blob(state.get("messages", []))
    lowered_recent_text = recent_text.lower()

    external_gate = gates.get("external_corruption_gate")
    local_gate = gates.get("local_corruption_exclusion")
    dma_gate_open = external_gate is not None and external_gate.status in {
        "open",
        "blocked",
    }
    local_exclusion_ready = local_gate is not None and local_gate.status in {
        "closed",
        "n/a",
    }

    if (
        signature_class in {"pointer_corruption", "use_after_free"}
        and step_count >= 10
        and (
            root_cause_class == "dma_corruption"
            or (dma_gate_open and local_exclusion_ready)
            or "dma" in lowered_recent_text
            or "iommu" in lowered_recent_text
        )
    ):
        fragments.append(SOP_FRAGMENTS["dma_corruption"])

    if any(
        token in lowered_recent_text for token in ("%gs", "per-cpu", "per_cpu", "gs:")
    ):
        fragments.append(SOP_FRAGMENTS["per_cpu_access"])

    if any(
        token in lowered_recent_text
        for token in ("search", "address", "ptov", "kmem -p")
    ):
        fragments.append(SOP_FRAGMENTS["address_search"])

    if (
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

    if (
        "stack overflow" in lowered_recent_text
        or "stack corruption" in lowered_recent_text
    ):
        fragments.append(SOP_FRAGMENTS["stack_overflow"])

    if "kasan" in lowered_recent_text or "ubsan" in lowered_recent_text:
        fragments.append(SOP_FRAGMENTS["kasan_ubsan"])

    if step_count >= 18 and signature_class in {
        "pointer_corruption",
        "use_after_free",
        "general_protection_fault",
    }:
        fragments.append(SOP_FRAGMENTS["advanced_techniques"])

    return _dedupe_preserve_order(fragments)


def _format_hypotheses(raw_hypotheses: object) -> str:
    hypotheses = cast(Optional[Sequence[Hypothesis]], raw_hypotheses)
    if not hypotheses:
        return "none"

    formatted = []
    for hypothesis in hypotheses[:3]:
        item = Hypothesis.model_validate(hypothesis)
        rank = f"#{item.rank}" if item.rank is not None else item.id
        formatted.append(f"{rank} {item.label} ({item.status})")
    return "; ".join(formatted)


def _format_gates(raw_gates: object) -> str:
    gates = cast(Optional[dict[str, GateEntry]], raw_gates)
    if not gates:
        return "none"

    formatted = []
    for gate_name, gate_entry in list(gates.items())[:5]:
        gate = GateEntry.model_validate(gate_entry)
        formatted.append(f"{gate_name}={gate.status}")
    return ", ".join(formatted)


def _recent_command_summaries(messages: Sequence[BaseMessage]) -> str:
    commands: list[str] = []
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue

        commands.extend(_extract_commands_from_ai_message(message))
        if len(commands) >= 8:
            break

    if not commands:
        return "none"

    unique_commands = _dedupe_preserve_order(reversed(commands))
    return "; ".join(unique_commands[-8:])


def _extract_commands_from_ai_message(message: AIMessage) -> list[str]:
    commands: list[str] = []

    for tool_call in message.tool_calls or []:
        name = tool_call.get("name") or tool_call.get("command_name")
        args = tool_call.get("args") or tool_call.get("arguments") or {}
        if isinstance(args, dict):
            if "command_name" in args:
                command_name = args["command_name"]
                command_args = args.get("arguments") or []
                commands.append(_render_command(command_name, command_args))
            elif "script" in args:
                commands.append(_render_command(str(name), [args["script"]]))
            elif "command" in args:
                commands.append(_render_command(str(name), [args["command"]]))
            else:
                commands.append(str(name))
        elif name:
            commands.append(_render_command(str(name), args))

    if commands:
        return commands

    try:
        parsed_content = json.loads(message.content)
    except (TypeError, json.JSONDecodeError):
        return []

    action = parsed_content.get("action")
    if not action:
        return []
    return [
        _render_command(
            action.get("command_name", "unknown"), action.get("arguments") or []
        )
    ]


def _render_command(command_name: str, arguments: object) -> str:
    if isinstance(arguments, str):
        rendered_args = arguments
    elif isinstance(arguments, Iterable):
        rendered_args = ", ".join(str(arg) for arg in arguments)
    else:
        rendered_args = ""

    return f"{command_name}({rendered_args})" if rendered_args else command_name


def _recent_text_blob(messages: Sequence[BaseMessage]) -> str:
    parts: list[str] = []
    for message in messages[-6:]:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            parts.append(content[:800])
    return "\n".join(parts)


def _infer_stage_name(
    step_count: int,
    gates: Optional[dict[str, GateEntry]],
) -> str:
    if step_count <= 3:
        return "Stage 0-1: panic classification and fault identification"

    if gates:
        open_gates = [
            name for name, gate in gates.items() if gate.status in {"open", "blocked"}
        ]
        if open_gates:
            return f"Stage 2-5: evidence collection ({open_gates[0]} pending)"

    if step_count >= 20:
        return "Stage 6: convergence and bounded conclusion"

    return "Stage 4-5: object validation and source exclusion"


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
