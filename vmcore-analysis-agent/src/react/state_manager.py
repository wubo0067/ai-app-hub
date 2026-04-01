#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from typing import Any, Dict, List, Optional, cast

from .schema import (
    CrashSignatureClass,
    GateEntry,
    Hypothesis,
    PartialDumpStatus,
    RootCauseClass,
    VMCoreAnalysisStep,
    VMCoreLLMAnalysisStep,
)


def project_managed_analysis_step(
    llm_step: VMCoreLLMAnalysisStep,
    state: Dict[str, Any],
    *,
    original_reasoning: str,
) -> tuple[VMCoreAnalysisStep, Dict[str, Any]]:
    signature_class = cast(
        Optional[CrashSignatureClass],
        llm_step.signature_class or state.get("current_signature_class"),
    )
    root_cause_class = cast(
        Optional[RootCauseClass],
        llm_step.root_cause_class or state.get("current_root_cause_class"),
    )
    partial_dump = _resolve_partial_dump(
        llm_step.partial_dump, state, original_reasoning
    )
    active_hypotheses = _build_managed_hypotheses(
        signature_class,
        root_cause_class,
        state.get("managed_active_hypotheses"),
    )
    gates = _build_managed_gates(
        signature_class,
        state.get("managed_gates"),
    )

    step = VMCoreAnalysisStep.model_validate(
        {
            **llm_step.model_dump(),
            "signature_class": signature_class,
            "root_cause_class": root_cause_class,
            "partial_dump": partial_dump,
            "active_hypotheses": (
                [hyp.model_dump() for hyp in active_hypotheses]
                if active_hypotheses
                else None
            ),
            "gates": (
                {name: gate.model_dump() for name, gate in gates.items()}
                if gates
                else None
            ),
        }
    )

    managed_updates: Dict[str, Any] = {
        "current_signature_class": signature_class,
        "current_root_cause_class": root_cause_class,
        "current_partial_dump": partial_dump,
        "managed_active_hypotheses": active_hypotheses,
        "managed_gates": gates,
    }
    return step, managed_updates


def _resolve_partial_dump(
    partial_dump: PartialDumpStatus,
    state: Dict[str, Any],
    original_reasoning: str,
) -> PartialDumpStatus:
    if partial_dump != "unknown":
        return partial_dump

    previous = state.get("current_partial_dump")
    if previous in {"full", "partial"}:
        return cast(PartialDumpStatus, previous)

    lowered_reasoning = original_reasoning.lower()
    if "[partial dump]" in lowered_reasoning:
        return "partial"
    if "dump is complete" in lowered_reasoning:
        return "full"
    return "unknown"


def _build_managed_hypotheses(
    signature_class: Optional[CrashSignatureClass],
    root_cause_class: Optional[RootCauseClass],
    prior_hypotheses: Optional[List[Hypothesis]],
) -> Optional[List[Hypothesis]]:
    leading_label = (
        root_cause_class
        if root_cause_class and root_cause_class != "unknown"
        else signature_class
    )
    if leading_label is None or leading_label == "unknown":
        return prior_hypotheses

    managed: List[Hypothesis] = [
        Hypothesis(
            id="H1",
            label=leading_label,
            rank=1,
            status="leading",
            evidence=(
                "Managed by executor state from root_cause_class."
                if root_cause_class and root_cause_class != "unknown"
                else "Managed by executor state from signature_class."
            ),
        )
    ]

    for prior in prior_hypotheses or []:
        if prior.label == leading_label:
            continue
        managed.append(
            Hypothesis(
                id=f"H{len(managed) + 1}",
                label=prior.label,
                rank=len(managed) + 1,
                status="candidate" if prior.status == "leading" else prior.status,
                evidence=prior.evidence,
            )
        )
    return managed


def _build_managed_gates(
    signature_class: Optional[CrashSignatureClass],
    prior_gates: Optional[Dict[str, GateEntry]],
) -> Optional[Dict[str, GateEntry]]:
    if signature_class is None or signature_class == "unknown":
        return prior_gates

    required = VMCoreAnalysisStep._REQUIRED_GATES.get(signature_class, [])
    if not required:
        return None

    managed: Dict[str, GateEntry] = {}
    for gate_name in required:
        previous = (prior_gates or {}).get(gate_name)
        if previous is not None:
            managed[gate_name] = GateEntry.model_validate(previous.model_dump())
            continue

        if gate_name == "external_corruption_gate":
            managed[gate_name] = GateEntry(
                required_for=[signature_class],
                status="blocked",
                prerequisite="local_corruption_exclusion",
                evidence="Managed by executor state: waiting for local_corruption_exclusion to close.",
            )
        elif gate_name == "field_type_classification":
            managed[gate_name] = GateEntry(
                required_for=[signature_class],
                status="open",
                evidence=(
                    "Managed by executor state: awaiting source-level field typing via "
                    "function-pointer anchoring, source cross-reference, or defensible offset inference."
                ),
            )
        else:
            managed[gate_name] = GateEntry(
                required_for=[signature_class],
                status="open",
                evidence="Managed by executor state: awaiting deterministic evidence.",
            )

    external_gate = managed.get("external_corruption_gate")
    local_gate = managed.get("local_corruption_exclusion")
    if external_gate is not None and external_gate.status not in ("closed", "n/a"):
        if local_gate is None or local_gate.status not in ("closed", "n/a"):
            managed["external_corruption_gate"] = GateEntry(
                required_for=external_gate.required_for,
                status="blocked",
                prerequisite="local_corruption_exclusion",
                evidence=external_gate.evidence
                or "Managed by executor state: waiting for local_corruption_exclusion to close.",
            )
        elif external_gate.status == "blocked":
            managed["external_corruption_gate"] = GateEntry(
                required_for=external_gate.required_for,
                status="open",
                prerequisite="local_corruption_exclusion",
                evidence="Managed by executor state: prerequisite satisfied; awaiting deterministic evidence.",
            )
    return managed
