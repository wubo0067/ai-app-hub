#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import Optional

from src.mcp_tools.crash.executor import run_crash_command

_BT_HEADER_RE = re.compile(
    r'^PID:\s+(?P<pid>\d+)\s+TASK:\s+\S+\s+CPU:\s+(?P<cpu>\d+)\s+COMMAND:\s+"(?P<comm>[^"]+)"'
)
_BT_FRAME_RE = re.compile(
    r"^\s*#(?P<num>\d+)\s+\[(?P<frame>[0-9a-fA-F]+)\]\s+(?P<func>\S+)\s+at\s+(?P<rip>[0-9a-fA-F]+)"
)
_DISASM_RE = re.compile(
    r"^0x(?P<addr>[0-9a-fA-F]+)\s+<(?P<sym>[^>]+)>:\s+(?P<inst>.+)$"
)
_CANARY_STORE_RE = re.compile(
    r"mov\s+%[a-z0-9]+,\s*-(?P<offset>0x[0-9a-fA-F]+)\(%rbp\)"
)
_RBP_PROLOGUE_PUSH_RE = re.compile(r"\bpush\s+%rbp\b")
_RBP_PROLOGUE_MOV_RE = re.compile(r"\bmov\s+%rsp,\s*%rbp\b")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_RD_LINE_RE = re.compile(r"^(?P<addr>[0-9a-fA-F]+):\s+(?P<words>.+)$")
_PER_CPU_RE = re.compile(r"\$\d+\s*=\s*(?P<addr>0x[0-9a-fA-F]+)")
_SYM_FUNC_RE = re.compile(
    r"^(?P<addr>[0-9a-fA-F]+)\s+\([A-Za-z]\)\s+(?P<symbol>[^\s+]+)"
)


@dataclass
class BtFrame:
    num: int
    frame_addr: int
    function: str
    rip: int


def parse_stack_canary_command(
    command: str,
) -> tuple[str, Optional[int], Optional[int]]:
    tokens = shlex.split(command)
    if not tokens:
        raise ValueError(
            "stack canary tool requires a canary-bearing function name, for example: 'search_module_extables'"
        )

    function_name = tokens[0]
    panic_return_address: Optional[int] = None
    stack_chk_fail_frame: Optional[int] = None

    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--panic-return-address":
            index += 1
            panic_return_address = _parse_hex_arg(tokens, index, token)
        elif token == "--stack-chk-fail-frame":
            index += 1
            stack_chk_fail_frame = _parse_hex_arg(tokens, index, token)
        else:
            raise ValueError(f"Unsupported option for stack canary tool: {token}")
        index += 1

    return function_name, panic_return_address, stack_chk_fail_frame


def resolve_stack_canary(
    vmcore_path: str,
    vmlinux_path: str,
    command: str,
) -> str:
    function_name, panic_return_address, stack_chk_fail_frame_override = (
        parse_stack_canary_command(command)
    )

    bt_output = run_crash_command("bt", vmcore_path, vmlinux_path, True)
    header, frames = _parse_bt(bt_output)

    stack_chk_frame = next(
        (frame for frame in frames if frame.function == "__stack_chk_fail"), None
    )
    if stack_chk_frame is None:
        raise ValueError("Failed to locate __stack_chk_fail in bt output.")

    canary_frame = next(
        (frame for frame in frames if frame.function == function_name), None
    )
    if canary_frame is None:
        raise ValueError(
            f"Failed to locate canary-bearing function '{function_name}' in bt output."
        )

    stack_chk_fail_frame_addr = (
        stack_chk_fail_frame_override
        if stack_chk_fail_frame_override is not None
        else stack_chk_frame.frame_addr
    )

    canary_disassembly = run_crash_command(
        f"dis -rl {function_name}", vmcore_path, vmlinux_path, True
    )
    stack_chk_disassembly = run_crash_command(
        "dis -rl __stack_chk_fail", vmcore_path, vmlinux_path, True
    )

    canary_offset = _extract_canary_offset(canary_disassembly)
    if canary_offset is None:
        raise ValueError(
            f"Failed to find a canonical canary store instruction in disassembly of {function_name}."
        )

    if not _has_standard_frame_pointer_prologue(stack_chk_disassembly):
        raise ValueError(
            "__stack_chk_fail does not show a standard frame-pointer prologue in disassembly."
        )

    derived_return_address = _extract_stack_chk_fail_return_address(canary_disassembly)
    if panic_return_address is None:
        if derived_return_address is None:
            raise ValueError(
                f"Failed to derive the return address after __stack_chk_fail from {function_name} disassembly. Use --panic-return-address <addr>."
            )
        panic_return_address = derived_return_address

    stack_dump = run_crash_command(
        f"rd -x {stack_chk_fail_frame_addr:x} 64", vmcore_path, vmlinux_path, True
    )
    stack_words = _parse_rd_words(stack_dump)
    return_location = _find_return_address_location(
        stack_words, panic_return_address, stack_chk_fail_frame_addr
    )
    if return_location is None:
        raise ValueError(
            f"Failed to find return address {panic_return_address:x} near __stack_chk_fail frame {stack_chk_fail_frame_addr:x}."
        )

    stack_chk_fail_rbp = return_location - 8
    canary_function_rbp = stack_words.get(stack_chk_fail_rbp)
    if canary_function_rbp is None:
        rbp_dump = run_crash_command(
            f"rd -x {stack_chk_fail_rbp:x} 2", vmcore_path, vmlinux_path, True
        )
        canary_function_rbp = _parse_rd_words(rbp_dump).get(stack_chk_fail_rbp)

    if canary_function_rbp is None:
        raise ValueError(f"Failed to read saved caller RBP at {stack_chk_fail_rbp:x}.")

    canary_slot_addr = canary_function_rbp - canary_offset
    canary_slot_dump = run_crash_command(
        f"rd -x {canary_slot_addr:x} 1", vmcore_path, vmlinux_path, True
    )
    canary_slot_value = _parse_rd_words(canary_slot_dump).get(canary_slot_addr)

    per_cpu_output = run_crash_command(
        f"p/x __per_cpu_offset[{header['cpu']}]", vmcore_path, vmlinux_path, True
    )
    per_cpu_base = _parse_per_cpu_offset(per_cpu_output)
    live_canary_addr = per_cpu_base + 0x28
    live_canary_dump = run_crash_command(
        f"rd -x {live_canary_addr:x} 1", vmcore_path, vmlinux_path, True
    )
    live_canary_value = _parse_rd_words(live_canary_dump).get(live_canary_addr)

    status = "unproven"
    if canary_slot_value is not None and live_canary_value is not None:
        status = "intact" if canary_slot_value == live_canary_value else "overwritten"

    result = {
        "success": True,
        "panic_task": header,
        "canary_function": function_name,
        "stack_chk_fail_frame_addr": _hex(stack_chk_fail_frame_addr),
        "canary_function_frame_addr": _hex(canary_frame.frame_addr),
        "canary_offset": _hex(canary_offset),
        "panic_return_address": _hex(panic_return_address),
        "frame_pointer_chain": {
            "return_address_location": _hex(return_location),
            "stack_chk_fail_rbp": _hex(stack_chk_fail_rbp),
            "saved_canary_function_rbp": _hex(canary_function_rbp),
        },
        "canary_slot": {
            "address": _hex(canary_slot_addr),
            "value": _hex_or_none(canary_slot_value),
            "live_canary_address": _hex(live_canary_addr),
            "live_canary_value": _hex_or_none(live_canary_value),
            "status": status,
        },
        "evidence": {
            "stack_chk_fail_disassembly_verified": True,
            "canary_disassembly_has_store": True,
            "stack_dump_window_start": _hex(stack_chk_fail_frame_addr),
            "stack_dump_window_words": 64,
        },
    }
    return json.dumps(result, indent=2)


def classify_saved_rip_frames(
    vmcore_path: str,
    vmlinux_path: str,
    command: str,
) -> str:
    start_frame, end_frame = parse_saved_rip_classification_command(command)

    bt_output = run_crash_command("bt", vmcore_path, vmlinux_path, True)
    header, frames = _parse_bt(bt_output)
    selected_frames = [
        frame
        for frame in frames
        if (start_frame is None or frame.num >= start_frame)
        and (end_frame is None or frame.num <= end_frame)
    ]
    if not selected_frames:
        raise ValueError("No bt frames selected for saved-RIP classification.")

    frame_window_start = min(frame.frame_addr for frame in selected_frames)
    frame_window_end = max(frame.frame_addr for frame in selected_frames) + 8
    word_count = max(8, ((frame_window_end - frame_window_start) // 8) + 8)
    stack_dump = run_crash_command(
        f"rd -x {frame_window_start:x} {word_count}", vmcore_path, vmlinux_path, True
    )
    stack_words = _parse_rd_words(stack_dump)

    inspection = []
    first_unreliable = None
    last_trusted = None
    previous_saved_rip = None
    previous_frame_num = None

    selected_by_num = sorted(selected_frames, key=lambda frame: frame.num)
    frame_by_num = {frame.num: frame for frame in frames}

    for frame in selected_by_num:
        saved_rip_value = stack_words.get(frame.frame_addr)
        if saved_rip_value is None:
            saved_rip_dump = run_crash_command(
                f"rd -x {frame.frame_addr:x} 1", vmcore_path, vmlinux_path, True
            )
            saved_rip_value = _parse_rd_words(saved_rip_dump).get(frame.frame_addr)

        saved_rip_symbol = None
        if saved_rip_value is not None:
            saved_rip_symbol = _resolve_symbol_name(
                run_crash_command(
                    f"sym {saved_rip_value:x}", vmcore_path, vmlinux_path, True
                )
            )

        expected_caller = frame_by_num.get(frame.num + 1)
        expected_caller_name = expected_caller.function if expected_caller else None
        duplicate_with_previous = (
            previous_saved_rip is not None and saved_rip_value == previous_saved_rip
        )

        classification = "unknown"
        reason = "insufficient evidence"
        if saved_rip_value is None:
            classification = "unreadable"
            reason = "saved RIP could not be read from raw stack"
        elif expected_caller_name and saved_rip_symbol == expected_caller_name:
            classification = "plausible_caller"
            reason = "saved RIP resolves inside the next outer frame"
        elif saved_rip_symbol == frame.function:
            classification = "self_referential"
            reason = "saved RIP resolves inside the current frame itself"
        elif duplicate_with_previous:
            classification = "duplicate_saved_rip"
            reason = (
                f"saved RIP duplicates frame #{previous_frame_num}"
                if previous_frame_num is not None
                else "saved RIP duplicates the previous frame"
            )
        elif expected_caller_name and saved_rip_symbol is not None:
            classification = "caller_mismatch"
            reason = f"saved RIP resolves to {saved_rip_symbol}, expected caller {expected_caller_name}"
        elif saved_rip_symbol is None:
            classification = "unresolved_saved_rip"
            reason = "saved RIP does not resolve to a kernel symbol"

        is_reliable = classification == "plausible_caller"
        if is_reliable:
            last_trusted = frame.num
        elif first_unreliable is None:
            first_unreliable = frame.num

        inspection.append(
            {
                "frame_num": frame.num,
                "frame_addr": _hex(frame.frame_addr),
                "frame_function": frame.function,
                "saved_rip_value": _hex_or_none(saved_rip_value),
                "saved_rip_symbol": saved_rip_symbol,
                "expected_caller_function": expected_caller_name,
                "classification": classification,
                "reason": reason,
                "duplicate_with_previous": duplicate_with_previous,
            }
        )

        previous_saved_rip = saved_rip_value
        previous_frame_num = frame.num

    mechanism_hint = "none"
    suspicious = [
        item
        for item in inspection
        if item["classification"]
        in {"self_referential", "duplicate_saved_rip", "caller_mismatch"}
    ]
    if any(item["classification"] == "duplicate_saved_rip" for item in suspicious):
        mechanism_hint = "stack_smearing_likely"
    elif len(suspicious) == 1:
        mechanism_hint = "single_saved_rip_corruption_likely"
    elif suspicious:
        mechanism_hint = "frame_reliability_problem_detected"

    result = {
        "success": True,
        "panic_task": header,
        "frame_range": {
            "start": start_frame,
            "end": end_frame,
        },
        "last_trusted_frame": last_trusted,
        "first_unreliable_frame": first_unreliable,
        "mechanism_hint": mechanism_hint,
        "inspected_frames": inspection,
    }
    return json.dumps(result, indent=2)


def parse_saved_rip_classification_command(
    command: str,
) -> tuple[Optional[int], Optional[int]]:
    tokens = shlex.split(command)
    start_frame: Optional[int] = None
    end_frame: Optional[int] = None

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--start-frame":
            index += 1
            start_frame = _parse_int_arg(tokens, index, token)
        elif token == "--end-frame":
            index += 1
            end_frame = _parse_int_arg(tokens, index, token)
        else:
            raise ValueError(
                f"Unsupported option for saved-RIP classification tool: {token}"
            )
        index += 1

    return start_frame, end_frame


def _parse_hex_arg(tokens: list[str], index: int, option_name: str) -> int:
    if index >= len(tokens):
        raise ValueError(f"Missing value for {option_name}")
    value = tokens[index].lower().removeprefix("0x")
    if not _HEX_RE.match(value):
        raise ValueError(f"Invalid hex value for {option_name}: {tokens[index]}")
    return int(value, 16)


def _parse_int_arg(tokens: list[str], index: int, option_name: str) -> int:
    if index >= len(tokens):
        raise ValueError(f"Missing value for {option_name}")
    try:
        return int(tokens[index], 10)
    except ValueError as exc:
        raise ValueError(
            f"Invalid integer value for {option_name}: {tokens[index]}"
        ) from exc


def _parse_bt(bt_output: str) -> tuple[dict[str, object], list[BtFrame]]:
    header: Optional[dict[str, object]] = None
    frames: list[BtFrame] = []

    for line in bt_output.splitlines():
        if header is None:
            header_match = _BT_HEADER_RE.search(line)
            if header_match:
                header = {
                    "pid": int(header_match.group("pid")),
                    "cpu": int(header_match.group("cpu")),
                    "comm": header_match.group("comm"),
                }
                continue

        frame_match = _BT_FRAME_RE.match(line)
        if frame_match:
            frames.append(
                BtFrame(
                    num=int(frame_match.group("num")),
                    frame_addr=int(frame_match.group("frame"), 16),
                    function=frame_match.group("func"),
                    rip=int(frame_match.group("rip"), 16),
                )
            )

    if header is None:
        raise ValueError("Failed to parse bt header.")
    if not frames:
        raise ValueError("Failed to parse bt frames.")
    return header, frames


def _extract_canary_offset(disassembly: str) -> Optional[int]:
    for line in disassembly.splitlines():
        match = _DISASM_RE.match(line)
        if not match:
            continue
        inst = match.group("inst")
        canary_match = _CANARY_STORE_RE.search(inst)
        if canary_match:
            return int(canary_match.group("offset"), 16)
    return None


def _extract_stack_chk_fail_return_address(disassembly: str) -> Optional[int]:
    instructions: list[tuple[int, str]] = []
    for line in disassembly.splitlines():
        match = _DISASM_RE.match(line)
        if not match:
            continue
        instructions.append((int(match.group("addr"), 16), match.group("inst")))

    for index, (_, inst) in enumerate(instructions):
        if "<__stack_chk_fail>" in inst and index + 1 < len(instructions):
            return instructions[index + 1][0]
    return None


def _has_standard_frame_pointer_prologue(disassembly: str) -> bool:
    instructions: list[str] = []
    for line in disassembly.splitlines():
        match = _DISASM_RE.match(line)
        if match:
            instructions.append(match.group("inst"))
        if len(instructions) >= 4:
            break
    has_push = any(_RBP_PROLOGUE_PUSH_RE.search(inst) for inst in instructions)
    has_mov = any(_RBP_PROLOGUE_MOV_RE.search(inst) for inst in instructions)
    return has_push and has_mov


def _parse_rd_words(output: str) -> dict[int, int]:
    words: dict[int, int] = {}
    for line in output.splitlines():
        match = _RD_LINE_RE.match(line.strip())
        if not match:
            continue
        base = int(match.group("addr"), 16)
        for index, word in enumerate(match.group("words").split()):
            cleaned = word.lower().removeprefix("0x")
            if _HEX_RE.match(cleaned):
                words[base + index * 8] = int(cleaned, 16)
    return words


def _find_return_address_location(
    stack_words: dict[int, int], target: int, stack_chk_fail_frame_addr: int
) -> Optional[int]:
    matches = sorted(addr for addr, value in stack_words.items() if value == target)
    if not matches:
        return None
    eligible = [addr for addr in matches if addr >= stack_chk_fail_frame_addr]
    return eligible[0] if eligible else matches[0]


def _parse_per_cpu_offset(output: str) -> int:
    match = _PER_CPU_RE.search(output)
    if match is None:
        raise ValueError("Failed to parse __per_cpu_offset output.")
    return int(match.group("addr"), 16)


def _resolve_symbol_name(output: str) -> Optional[str]:
    for line in output.splitlines():
        match = _SYM_FUNC_RE.match(line.strip())
        if match:
            return match.group("symbol")
    return None


def _hex(value: int) -> str:
    return f"0x{value:x}"


def _hex_or_none(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    return _hex(value)
