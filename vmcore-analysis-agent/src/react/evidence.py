"""Structured evidence extraction and set-difference helpers for crash output."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

_HEX = r"(?:0x)?[0-9a-fA-F]+"
_RD_LINE_RE = re.compile(rf"^\s*(?P<address>{_HEX})\s*:\s*(?P<words>.*)$")
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
        gate = (
            raw_gate.model_copy(deep=True)
            if hasattr(raw_gate, "model_copy")
            else raw_gate
        )
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
        if prerequisite is None or getattr(prerequisite, "status", None) not in {
            "closed",
            "n/a",
        }:
            return False
    return sum(
        any(categories[name] for name in group) for group in required_groups
    ) >= len(required_groups)


def extract_evidence_facts(
    tool_name: str,
    raw_args: Any,
    output: str,
) -> set[str]:
    """提取出的事实用于后续的门控（gate）闭合评估，以判断某个诊断假设是否
    有足够的结构化证据支撑。

    Args:
        tool_name: 工具名称，通常为 "run_script"（表示执行多行脚本）或其他直接命令工具。
                该参数决定了如何从 raw_args 中解析出实际的命令列表。
        raw_args: 工具的原始参数字典或字符串。
                - 对于直接命令工具：通常是 {"command": "rd 0x100 8"} 或纯字符串。
                - 对于 run_script：通常是 {"script": "rd 0x100 8\nstruct task_struct"} 或纯字符串。
        output: crash 调试器命令执行后的原始标准输出文本。

    Returns:
        set[str]: 提取出的事实字符串集合。每个事实是一个稳定的、可哈希的字符串，
        格式取决于命令类型：
        - rd 命令 → "rd_word:0x<address>=0x<value>"
        - struct 命令 → "struct_type:<name>"、"struct_field:<name>.<field>@0x<offset>"、
                        "struct_size:<name>=0x<size>"
        - dis 命令 → "dis_instruction:0x<address>=<mnemonic>"、"dis_symbol:<symbol>"
        - sym 命令 → "sym:<symbol>@0x<address>:<kind>"

        如果命令类型不受支持或输出中无有效内容，则返回空集合。
    """
    # 根据工具类型和参数，解析出实际执行的 crash 命令列表。
    # 例如 run_script 可能包含多行命令，每行会被拆分为独立命令。
    commands = _command_lines(tool_name, raw_args)
    facts: set[str] = set()
    for command in commands:
        # 提取命令的第一个单词（命令名），转为小写以统一匹配
        # 例如 "rd 0x100 8" → "rd"，"struct task_struct" → "struct"
        name = command.split(maxsplit=1)[0].lower() if command.strip() else ""
        if name == "rd":
            # 解析内存读取输出，提取每个 8 字节字及其地址
            facts.update(_parse_rd(output))
        elif name == "struct":
            # 解析结构体定义输出，提取类型名、字段偏移和总大小
            facts.update(_parse_struct(output))
        elif name == "dis":
            # 解析反汇编输出，提取指令助记符和符号
            facts.update(_parse_dis(output))
        elif name == "sym":
            # 解析符号表输出，提取符号名、地址和类型
            facts.update(_parse_sym(output))
    return facts


def update_gate_evidence(
    gates: Mapping[str, Any] | None,
    facts: Iterable[str],
) -> dict[str, Any] | None:
    """
    将新发现的事实（facts）附加到相关的门控（gates）证据中，但不允许事实直接关闭门控。

    该函数会遍历所有的门控，检查传入的事实是否支持某个门控。如果支持，则将该事实
    以特定的标记格式 `[evidence-delta] <fact>` 追加到该门控的 `evidence` 字段中。
    此操作仅用于记录证据的增加，不会改变门控的状态（例如不会将其设为已关闭）。

    Args:
        gates: 一个映射，键为门控名称，值为门控对象（通常是 Pydantic 模型或类字典对象）。
               如果为 None，则返回 None。
        facts: 一个可迭代的事实字符串集合。

    Returns:
        包含更新后门控对象的字典，或者如果输入 gates 为 None 则返回 None。
        如果 facts 为空，则返回 gates 的副本。
    """
    if not gates:
        return None

    # 对事实进行去重并排序，确保处理顺序的一致性
    fact_list = sorted(set(facts))
    if not fact_list:
        return dict(gates)

    updated: dict[str, Any] = {}
    for gate_name, raw_gate in gates.items():
        # 深度拷贝门控对象，以避免直接修改原始输入数据
        # 优先使用 Pydantic 的 model_copy 方法，否则进行浅拷贝/直接赋值
        gate = (
            raw_gate.model_copy(deep=True)
            if hasattr(raw_gate, "model_copy")
            else raw_gate
        )

        # 筛选出能够支持当前门控的所有事实
        relevant = [fact for fact in fact_list if _fact_supports_gate(fact, gate_name)]

        if relevant:
            # 获取现有的证据内容，如果不存在则初始化为空字符串
            evidence = getattr(gate, "evidence", None) or ""
            marker = "[evidence-delta] "
            existing = set(evidence.splitlines())

            # 构造新的证据行，并过滤掉已经存在的证据，防止重复添加
            additions = [
                f"{marker}{fact}"
                for fact in relevant
                if f"{marker}{fact}" not in existing
            ]

            if additions:
                # 将新证据追加到现有证据末尾，并去除首尾空格
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
    """
    解析由内核调试器（如 gdb/crash）输出的结构体定义文本，并将其转换为事实（facts）集合。

    该函数通过正则表达式识别结构体的名称、字段及其偏移量，以及结构体的总大小。
    生成的 fact 格式如下：
    - struct_type:<name>: 表示发现了一个结构体类型。
    - struct_field:<name>.<field_name>@0x<offset>: 表示结构体中的一个字段及其偏移量。
    - struct_size:<name>=0x<size>: 表示结构体的总字节大小。

    Args:
        output (str): 包含结构体定义的原始字符串输出。

    Returns:
        set[str]: 包含解析出的结构体相关事实的集合。
    """
    facts: set[str] = set()
    current_type: str | None = None

    for line in output.splitlines():
        # 尝试匹配结构体头部，例如 "struct task_struct {"
        header = _STRUCT_HEADER_RE.match(line)
        if header:
            current_type = header.group("name")
            facts.add(f"struct_type:{current_type}")
            continue

        # 如果当前不在任何结构体定义块内，则跳过该行
        if current_type is None:
            continue

        # 尝试匹配结构体字段，例如 "    int state; /* offset 0x10 */"
        field = _STRUCT_FIELD_RE.match(line)
        if field:
            offset = _to_struct_int(field.group("offset"))
            facts.add(
                f"struct_field:{current_type}.{field.group('field')}@0x{offset:x}"
            )
            continue

        # 尝试匹配结构体结束时的总大小，例如 "} size: 0x1234"
        size = _STRUCT_SIZE_RE.match(line)
        if size:
            facts.add(
                f"struct_size:{current_type}=0x{_to_struct_int(size.group('size')):x}"
            )
            # 解析完一个结构体，重置当前类型，防止后续行被错误归类
            current_type = None

    return facts


def _parse_dis(output: str) -> set[str]:
    """
    解析反汇编（disassembly）输出并提取事实（facts）。

    该函数通过正则表达式匹配反汇编输出的每一行，提取指令地址、指令内容以及符号信息。
    它会将提取到的信息转化为特定格式的字符串，存入事实集合中。

    解析出的事实格式：
    - `dis_instruction:0x<address>=<mnemonic>`: 记录特定地址处的指令助记符。
    - `dis_symbol:<symbol>`: 记录反汇编输出中出现的符号。

    Args:
        output (str): 反汇编工具（如 objdump）的标准输出字符串。

    Returns:
        set[str]: 包含提取出的反汇编相关事实的集合。
    """
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
    """
    解析符号表（symbol table）输出并提取事实（facts）。

    该函数解析符号表工具（如 nm）的输出，提取符号名称、内存地址及其类型（kind）。

    解析出的事实格式：
    - `sym:<symbol>@0x<address>:<kind>`: 记录符号及其对应的地址和类型。

    Args:
        output (str): 符号表工具的标准输出字符串。

    Returns:
        set[str]: 包含提取出的符号相关事实的集合。
    """
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
