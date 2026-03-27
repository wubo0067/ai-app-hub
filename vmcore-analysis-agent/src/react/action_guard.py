#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# action_guard.py - crash action executor 校验与规范化模块

import re
from typing import Any, Iterable, List, Optional

_HEAD_TAIL_SUFFIX_RE = re.compile(r"\s*\|\s*(?:head|tail)\s+-\d+\s*$")
_FORBIDDEN_EXPR_RE = re.compile(
    r"\$\(|\$\(\(|\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z0-9]+:|\(%[A-Za-z0-9]+\)|%rip\+|\$r[A-Za-z0-9]+"
)
_ADDRESS_ARITHMETIC_RE = re.compile(
    r"^(?:rd|ptov|vtop|struct|sym|kmem)\b.*(?:0x[0-9A-Fa-f]+|\b[0-9A-Fa-f]{8,}\b)\+\s*(?:0x[0-9A-Fa-f]+|\d+)"
)
_MODULE_SYMBOL_PREFIXES = (
    "mlx5_",
    "nvme_",
    "pqi_",
    "qla2xxx_",
    "mpt3sas_",
)
_DISASM_LINE_RE = re.compile(r"^\s*0x[0-9a-fA-F]+\s+<[^>]+>:\s+(?P<inst>.+)$")
_MOV_ALIAS_RE = re.compile(
    r"\bmov[a-z]*\s+%(?P<src>r(?:1[0-5]|[0-9]|[a-z]{2,3})),\s*%(?P<dst>r(?:1[0-5]|[0-9]|[a-z]{2,3}))"
)
_MEMORY_OPERAND_RE = re.compile(
    r"(?:(?P<disp>0x[0-9a-fA-F]+))?\(%(?P<base>r(?:1[0-5]|[0-9]|[a-z]{2,3}))"
)
_STRUCT_LAYOUT_HEADER_RE = re.compile(r"^struct\s+(?P<type_name>\S+)\s+\{$")
_STRUCT_FIELD_OFFSET_RE = re.compile(r"^\s*\[(?P<offset>\d+)\]\s+")
_STRUCT_SIZE_RE = re.compile(r"^SIZE:\s+(?P<size>\d+)")


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.strip().split())


def canonicalize_command_line(command_line: str) -> str:
    normalized = _collapse_whitespace(command_line)
    while True:
        stripped = _HEAD_TAIL_SUFFIX_RE.sub("", normalized)
        if stripped == normalized:
            break
        normalized = stripped.strip()

    parts = normalized.split()
    if parts[:1] == ["struct"] and "-o" in parts[1:]:
        normalized = " ".join(
            ["struct", "-o", *[part for part in parts[1:] if part != "-o"]]
        )
    return normalized


def extract_command_lines(tool_name: str, args: Any) -> List[str]:
    if tool_name == "run_script":
        script = args.get("script", "") if isinstance(args, dict) else str(args)
        return [line.strip() for line in str(script).splitlines() if line.strip()]

    if isinstance(args, dict):
        command = str(args.get("command", "")).strip()
    else:
        command = str(args).strip()
    return [command] if command else []


def build_command_fingerprint(tool_name: str, args: Any) -> str:
    return build_fingerprint_from_lines(extract_command_lines(tool_name, args))


def build_fingerprint_from_lines(lines: Iterable[str]) -> str:
    substantive_lines = []
    for line in lines:
        canonical = canonicalize_command_line(line)
        if not canonical or canonical.startswith("mod -s "):
            continue
        substantive_lines.append(canonical)
    return "\n".join(substantive_lines)


def validate_tool_call_request(
    tool_name: str,
    args: Any,
    *,
    allow_bt_a: bool = False,
    observed_struct_offsets: Optional[Iterable[int]] = None,
    struct_layout_cache: Optional[dict[str, dict[str, Any]]] = None,
) -> str | None:
    lines = extract_command_lines(tool_name, args)
    if tool_name == "run_script" and not lines:
        return "run_script must contain at least one command line."

    for line in lines:
        error = _validate_command_line(line, allow_bt_a=allow_bt_a)
        if error:
            return error

    if tool_name == "run_script":
        substantive_lines = [
            line
            for line in lines
            if not canonicalize_command_line(line).startswith("mod -s ")
        ]
        if not substantive_lines:
            return "run_script cannot contain only mod -s; include at least one diagnostic command."

        if _uses_module_specific_symbol(substantive_lines):
            first_line = canonicalize_command_line(lines[0])
            if not first_line.startswith("mod -s "):
                return "run_script uses module-specific symbols/types and must start with mod -s <module> <path>."

        struct_error = _validate_struct_requests(
            substantive_lines,
            observed_struct_offsets=observed_struct_offsets,
            struct_layout_cache=struct_layout_cache,
        )
        if struct_error is not None:
            return struct_error

    return None


def _validate_command_line(command_line: str, *, allow_bt_a: bool) -> str | None:
    raw_parts = _collapse_whitespace(command_line).split()
    if raw_parts[:1] == ["struct"] and len(raw_parts) >= 3 and "-o" in raw_parts[2:]:
        return "struct offset queries must use struct -o <type>."

    normalized = canonicalize_command_line(command_line)
    if not normalized:
        return "empty crash command is not allowed."

    if _FORBIDDEN_EXPR_RE.search(normalized):
        return f"forbidden shell/register expression in crash command: {normalized}"

    if _ADDRESS_ARITHMETIC_RE.search(normalized):
        return f"address arithmetic must be resolved before execution: {normalized}"

    parts = normalized.split()
    command = parts[0]

    if command == "sym" and len(parts) > 1 and parts[1] == "-l":
        return "sym -l is forbidden; use sym <symbol>."

    if command == "bt" and "-a" in parts[1:] and not allow_bt_a:
        return "bt -a is forbidden unless the current signature_class is hard_lockup."

    if command == "log":
        if normalized == "log":
            return "standalone log is forbidden; pipe it with grep."
        if normalized.startswith("log |"):
            return "log must use log -m | grep <pattern>, not log | grep."
        if len(parts) >= 2 and parts[1] in {"-m", "-t", "-a"} and "|" not in normalized:
            return f"standalone {parts[0]} {parts[1]} is forbidden; pipe it with grep."

    if command == "kmem":
        if len(parts) == 1:
            return "kmem must include an option flag such as -i, -S, or -p."
        if parts[1] == "-a":
            return "kmem -a <addr> is forbidden; use kmem -S <addr>."
        if parts[1] == "-S" and len(parts) == 2:
            return "bare kmem -S is forbidden; use kmem -S <addr>."

    if command == "struct":
        if parts == ["struct", "-o"]:
            return "bare struct -o is forbidden; use struct -o <type>."

    if command == "rd":
        if all(part.startswith("-") for part in parts[1:]):
            return "rd command is incomplete; provide an address target."

    if command in {"ptov", "vtop", "sym"} and len(parts) == 1:
        return f"{command} requires a target operand."

    return None


def _uses_module_specific_symbol(lines: Iterable[str]) -> bool:
    for line in lines:
        lowered = canonicalize_command_line(line).lower()
        if any(prefix in lowered for prefix in _MODULE_SYMBOL_PREFIXES):
            return True
    return False


def extract_crash_path_struct_offsets(tool_output: str) -> list[int]:
    alias_parent: dict[str, str] = {}
    offsets_by_root: dict[str, set[int]] = {}

    def find(reg: str) -> str:
        parent = alias_parent.get(reg, reg)
        while parent != alias_parent.get(parent, parent):
            parent = alias_parent.get(parent, parent)
        alias_parent[reg] = parent
        return parent

    for line in tool_output.splitlines():
        match = _DISASM_LINE_RE.match(line)
        if match is None:
            continue

        instruction = match.group("inst")
        alias_match = _MOV_ALIAS_RE.search(instruction)
        if alias_match is not None:
            src = find(alias_match.group("src"))
            dst = alias_match.group("dst")
            alias_parent[dst] = src

        for mem_match in _MEMORY_OPERAND_RE.finditer(instruction):
            base = find(mem_match.group("base"))
            if base in {"rsp", "rbp", "rip"}:
                continue
            displacement = mem_match.group("disp")
            offset = int(displacement, 16) if displacement is not None else 0
            offsets_by_root.setdefault(base, set()).add(offset)

    if not offsets_by_root:
        return []

    dominant_root = max(
        offsets_by_root,
        key=lambda reg: (len(offsets_by_root[reg]), max(offsets_by_root[reg])),
    )
    return sorted(offsets_by_root[dominant_root])


def extract_struct_layouts(tool_output: str) -> dict[str, dict[str, Any]]:
    layouts: dict[str, dict[str, Any]] = {}
    current_type: Optional[str] = None
    current_offsets: list[int] = []

    for raw_line in tool_output.splitlines():
        line = raw_line.strip()
        header_match = _STRUCT_LAYOUT_HEADER_RE.match(line)
        if header_match is not None:
            current_type = header_match.group("type_name")
            current_offsets = []
            continue

        if current_type is None:
            continue

        field_match = _STRUCT_FIELD_OFFSET_RE.match(raw_line)
        if field_match is not None:
            current_offsets.append(int(field_match.group("offset")))
            continue

        size_match = _STRUCT_SIZE_RE.match(line)
        if size_match is not None:
            layouts[current_type] = {
                "size": int(size_match.group("size")),
                "field_offsets": sorted(set(current_offsets)),
            }
            current_type = None
            current_offsets = []

    return layouts


def _validate_struct_requests(
    lines: Iterable[str],
    *,
    observed_struct_offsets: Optional[Iterable[int]],
    struct_layout_cache: Optional[dict[str, dict[str, Any]]],
) -> str | None:
    observed_offsets = sorted({int(offset) for offset in observed_struct_offsets or []})
    if not observed_offsets:
        return None

    layout_cache = struct_layout_cache or {}
    offset_query_types: set[str] = set()
    instance_query_types: list[str] = []

    for line in lines:
        parts = canonicalize_command_line(line).split()
        if len(parts) < 2 or parts[0] != "struct":
            continue
        if len(parts) >= 3 and parts[1] == "-o":
            offset_query_types.add(parts[2])
            continue
        if parts[1] != "-o" and len(parts) >= 3:
            instance_query_types.append(parts[1])

    for type_name in instance_query_types:
        cached_layout = layout_cache.get(type_name)
        if cached_layout is None and type_name in offset_query_types:
            return (
                f"struct {type_name} <addr> cannot be combined with first-time struct -o {type_name} "
                "when crash-path offsets are already known; inspect the layout in one step, then issue "
                "struct <type> <addr> only after validating offset coverage."
            )

        if cached_layout is None:
            continue

        size = int(cached_layout.get("size", 0))
        field_offsets = {
            int(offset) for offset in cached_layout.get("field_offsets", [])
        }
        max_observed = max(observed_offsets)
        if max_observed >= size:
            return (
                f"struct type {type_name} is too small for the observed crash-path offsets "
                f"(max observed offset 0x{max_observed:x}, size 0x{size:x})."
            )

        uncovered_offsets = [
            offset for offset in observed_offsets if offset not in field_offsets
        ]
        if uncovered_offsets:
            uncovered_str = ", ".join(f"0x{offset:x}" for offset in uncovered_offsets)
            return (
                f"struct type {type_name} does not cover the observed crash-path field offsets {uncovered_str}; "
                "do not interpret this type until a compatible layout is validated."
            )

    return None
