"""Structured evidence extraction and set-difference helpers for crash output."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


_HEX = r"(?:0x)?[0-9a-fA-F]+"
_RD_LINE_RE = re.compile(
    rf"^\s*(?P<address>{_HEX})\s*:\s*(?P<words>.*)$"
)
_STRUCT_HEADER_RE = re.compile(r"^\s*struct\s+(?P<name>\S+)\s*\{")
_STRUCT_FIELD_RE = re.compile(
    rf"^\s*\[(?P<offset>{_HEX})\]\s+(?P<field>[A-Za-z_][\w.\[\]-]*)"
)
_STRUCT_SIZE_RE = re.compile(rf"^\s*SIZE\s*:\s*(?P<size>{_HEX})")
_DIS_LINE_RE = re.compile(
    rf"^\s*(?P<address>{_HEX})\s+<(?P<symbol>[^>]+)>:\s+(?P<instruction>\S+.*)$"
)
_SYM_LINE_RE = re.compile(
    rf"^\s*(?P<address>{_HEX})\s+(?P<kind>[A-Za-z?])\s+(?P<symbol>\S+)"
)

_GATE_COMPLETION_CRITERIA: dict[str, list[str]] = {
    "register_provenance": [
        "faulting register value is present",
        "source object or address evidence is present",
        "field/offset or symbol relation is independently observed",
    ],
    "object_lifetime": [
        "object memory evidence is present",
        "a second observation supports the object lifetime classification",
    ],
    "local_corruption_exclusion": [
        "a relevant disassembly observation is present",
        "a memory observation supports or excludes a local writer",
    ],
    "field_type_classification": [
        "struct field layout evidence is present",
        "a type or symbol observation corroborates the field classification",
    ],
    "external_corruption_gate": [
        "the local corruption exclusion prerequisite is closed",
        "at least one concrete external-source observation is present",
    ],
}


def gate_completion_criteria(gate_name: str) -> list[str]:
    """Return executor-owned, human-readable completion criteria for a gate."""
    return list(
        _GATE_COMPLETION_CRITERIA.get(
            gate_name, ["at least one concrete structured evidence fact is present"]
        )
    )


def evaluate_gate_closures(
    gates: Mapping[str, Any] | None,
    facts: Iterable[str],
    prior_gates: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, object]]]:
    """Apply executor-owned closure rules and return auditable gate transitions."""
    if not gates:
        return None, []

    fact_set = set(facts)
    prior_gates = prior_gates or {}
    evaluated: dict[str, Any] = {}
    transitions: list[dict[str, object]] = []
    for gate_name, raw_gate in gates.items():
        gate = raw_gate.model_copy(deep=True) if hasattr(raw_gate, "model_copy") else raw_gate
        prior = prior_gates.get(gate_name)
        prior_status = getattr(prior, "status", None)
        requested_status = getattr(gate, "status", "open")
        gate.completion_criteria = gate_completion_criteria(gate_name)

        if prior_status in {"closed", "n/a"}:
            gate.status = prior_status
        elif _gate_criteria_satisfied(gate_name, fact_set, evaluated):
            gate.status = "closed"
        elif requested_status == "closed":
            if not _gate_criteria_satisfied(gate_name, fact_set, evaluated):
                gate.status = prior_status or "open"
                transitions.append(
                    {
                        "gate_name": gate_name,
                        "from_status": prior_status or "open",
                        "to_status": "open",
                        "event": "llm_close_rejected",
                        "reason": "completion criteria not satisfied by structured evidence",
                        "evidence_facts": sorted(fact_set),
                    }
                )
        evaluated[gate_name] = gate

        current_status = getattr(gate, "status", "open")
        if prior_status is not None and current_status != prior_status:
            transitions.append(
                {
                    "gate_name": gate_name,
                    "from_status": prior_status,
                    "to_status": current_status,
                    "event": "gate_transition",
                    "reason": "evidence evaluator",
                    "evidence_facts": sorted(fact_set),
                }
            )

    return evaluated, transitions


def _gate_criteria_satisfied(
    gate_name: str,
    facts: set[str],
    evaluated_gates: Mapping[str, Any],
) -> bool:
    categories = {
        "rd": any(fact.startswith("rd_word:") for fact in facts),
        "struct": any(fact.startswith("struct_") for fact in facts),
        "dis": any(fact.startswith("dis_") for fact in facts),
        "sym": any(fact.startswith("sym:") for fact in facts),
    }
    required_groups = {
        "register_provenance": [{"rd"}, {"dis", "sym"}],
        "object_lifetime": [{"rd", "struct"}],
        "local_corruption_exclusion": [{"dis"}, {"rd"}],
        "field_type_classification": [{"struct"}, {"sym"}],
        "external_corruption_gate": [{"rd", "struct", "dis", "sym"}],
    }.get(gate_name, [{"rd", "struct", "dis", "sym"}])
    if gate_name == "external_corruption_gate":
        prerequisite = evaluated_gates.get("local_corruption_exclusion")
        if prerequisite is None or getattr(prerequisite, "status", None) not in {"closed", "n/a"}:
            return False
    return sum(any(categories[name] for name in group) for group in required_groups) >= len(
        required_groups
    )


def extract_evidence_facts(
    tool_name: str,
    raw_args: Any,
    output: str,
) -> set[str]:
    """Extract stable, deduplicable facts from supported crash command output."""
    commands = _command_lines(tool_name, raw_args)
    facts: set[str] = set()
    for command in commands:
        name = command.split(maxsplit=1)[0].lower() if command.strip() else ""
        if name == "rd":
            facts.update(_parse_rd(output))
        elif name == "struct":
            facts.update(_parse_struct(output))
        elif name == "dis":
            facts.update(_parse_dis(output))
        elif name == "sym":
            facts.update(_parse_sym(output))
    return facts


def update_gate_evidence(
    gates: Mapping[str, Any] | None,
    facts: Iterable[str],
) -> dict[str, Any] | None:
    """Attach new facts to the relevant gate without allowing facts to close it."""
    if not gates:
        return None
    fact_list = sorted(set(facts))
    if not fact_list:
        return dict(gates)

    updated: dict[str, Any] = {}
    for gate_name, raw_gate in gates.items():
        gate = raw_gate.model_copy(deep=True) if hasattr(raw_gate, "model_copy") else raw_gate
        relevant = [fact for fact in fact_list if _fact_supports_gate(fact, gate_name)]
        if relevant:
            evidence = getattr(gate, "evidence", None) or ""
            marker = "[evidence-delta] "
            existing = set(evidence.splitlines())
            additions = [f"{marker}{fact}" for fact in relevant if f"{marker}{fact}" not in existing]
            if additions:
                gate.evidence = "\n".join([evidence, *additions]).strip()
        updated[gate_name] = gate
    return updated


def facts_support_goal(facts: Iterable[str], goal: Mapping[str, Any] | None) -> bool:
    """Return whether at least one new fact is relevant to the current evidence goal."""
    if not goal:
        return False
    gate_name = str(goal.get("gate_name", ""))
    return any(_fact_supports_gate(fact, gate_name) for fact in facts)


def _command_lines(tool_name: str, raw_args: Any) -> list[str]:
    if tool_name != "run_script":
        if isinstance(raw_args, dict):
            command = raw_args.get("command", "")
            return [str(command)]
        return [str(raw_args)]
    if isinstance(raw_args, dict):
        script = raw_args.get("script", "")
    else:
        script = raw_args
    return [line.strip() for line in str(script).splitlines() if line.strip()]


def _parse_rd(output: str) -> set[str]:
    facts: set[str] = set()
    for line in output.splitlines():
        match = _RD_LINE_RE.match(line)
        if not match:
            continue
        address = _to_int(match.group("address"))
        words = []
        for token in match.group("words").split():
            if not re.fullmatch(_HEX, token):
                break
            words.append(token)
        for index, word in enumerate(words):
            facts.add(f"rd_word:0x{address + index * 8:x}=0x{_to_int(word):x}")
    return facts


def _parse_struct(output: str) -> set[str]:
    facts: set[str] = set()
    current_type: str | None = None
    for line in output.splitlines():
        header = _STRUCT_HEADER_RE.match(line)
        if header:
            current_type = header.group("name")
            facts.add(f"struct_type:{current_type}")
            continue
        if current_type is None:
            continue
        field = _STRUCT_FIELD_RE.match(line)
        if field:
            offset = _to_struct_int(field.group("offset"))
            facts.add(f"struct_field:{current_type}.{field.group('field')}@0x{offset:x}")
            continue
        size = _STRUCT_SIZE_RE.match(line)
        if size:
            facts.add(
                f"struct_size:{current_type}=0x{_to_struct_int(size.group('size')):x}"
            )
            current_type = None
    return facts


def _parse_dis(output: str) -> set[str]:
    facts: set[str] = set()
    for line in output.splitlines():
        match = _DIS_LINE_RE.match(line)
        if not match:
            continue
        address = _to_int(match.group("address"))
        instruction = " ".join(match.group("instruction").split())
        mnemonic = instruction.split(maxsplit=1)[0]
        facts.add(f"dis_instruction:0x{address:x}={mnemonic}")
        facts.add(f"dis_symbol:{match.group('symbol')}")
    return facts


def _parse_sym(output: str) -> set[str]:
    facts: set[str] = set()
    for line in output.splitlines():
        match = _SYM_LINE_RE.match(line)
        if match:
            facts.add(
                f"sym:{match.group('symbol')}@0x{_to_int(match.group('address')):x}:{match.group('kind')}"
            )
    return facts


def _fact_supports_gate(fact: str, gate_name: str) -> bool:
    if gate_name == "register_provenance":
        return fact.startswith(("rd_word:", "dis_", "sym:"))
    if gate_name == "local_corruption_exclusion":
        return fact.startswith(("dis_", "rd_word:"))
    if gate_name == "field_type_classification":
        return fact.startswith(("struct_", "sym:"))
    if gate_name == "object_lifetime":
        return fact.startswith(("rd_word:", "struct_"))
    return fact.startswith(("rd_word:", "struct_", "dis_", "sym:"))


def _to_int(value: str) -> int:
    return int(value, 16 if value.lower().startswith("0x") else 16)


def _to_struct_int(value: str) -> int:
    return int(value, 0) if value.lower().startswith("0x") else int(value, 10)
