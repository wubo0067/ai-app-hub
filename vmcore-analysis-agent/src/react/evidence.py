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
