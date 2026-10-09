"""Structured evidence extraction and set-difference helpers for crash output."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .consistency import parse_struct_layouts

_HEX = r"(?:0x)?[0-9a-fA-F]+"
_RD_LINE_RE = re.compile(rf"^\s*(?P<address>{_HEX})\s*:\s*(?P<words>.*)$")
_DIS_LINE_RE = re.compile(
    rf"^\s*(?P<address>{_HEX})\s+<(?P<symbol>[^>]+)>:\s+(?P<instruction>\S+.*)$"
)
_SYM_LINE_RE = re.compile(
    rf"^\s*(?P<address>{_HEX})\s+(?P<kind>[A-Za-z?])\s+(?P<symbol>\S+)"
)
_VTOP_UNMAPPED_RE = re.compile(
    rf"^\s*(?P<address>{_HEX})\s+\(not mapped\)\s*$",
    re.IGNORECASE,
)
_VTOP_PTE_RE = re.compile(
    rf"^\s*PTE:\s*(?:{_HEX}\s*=>\s*)?(?P<value>{_HEX})\s*$",
    re.IGNORECASE,
)
_KMEM_RANGE_RE = re.compile(
    rf"^\s*\S+\s+\S+\s+(?P<start>{_HEX})\s+-\s+(?P<end>{_HEX})\s+(?P<size>\d+)\s*$",
    re.IGNORECASE,
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
    """
    应用执行器拥有的闭合规则，并返回可审计的门控状态转换记录。

    该函数通过检查当前证据事实（facts）是否满足每个门控（gate）的预定义完成准则，
    来决定门控是否应该关闭。如果 LLM 尝试关闭一个证据不足的门控，系统会拒绝该请求。

    Args:
        gates: 当前所有门控的映射，键为门控名称，值为门控对象。
        facts: 当前观测到的结构化事实集合。
        prior_gates: 先前的门控状态映射，用于对比状态变化。

    Returns:
        tuple[dict[str, Any] | None, list[dict[str, object]]]:
            - 更新后的门控对象字典。
            - 记录了所有门控状态转换的列表（包含转换原因和证据事实）。
    """
    if not gates:  # 如果没有门控，直接返回
        return None, []

    fact_set = set(facts)  # 将事实转换为集合以提高查找效率
    prior_gates = prior_gates or {}  # 如果没有提供先前的门控，则初始化为空字典
    evaluated: dict[str, Any] = {}  # 存储评估后的门控对象
    transitions: list[dict[str, object]] = []  # 记录状态转换事件

    for gate_name, raw_gate in gates.items():
        # 深度拷贝门控对象，以避免修改原始数据
        gate = (
            raw_gate.model_copy(deep=True)
            if hasattr(raw_gate, "model_copy")
            else raw_gate
        )
        prior = prior_gates.get(gate_name)  # 获取该门控先前的状态
        prior_status = getattr(prior, "status", None)  # 先前的状态
        requested_status = getattr(gate, "status", "open")  # LLM 请求的状态
        gate.completion_criteria = gate_completion_criteria(
            gate_name
        )  # 设置该门控的完成准则

        if prior_status in {"closed", "n/a"}:
            # 如果门控之前已经是关闭或不可用状态，保持现状
            gate.status = prior_status
        elif _gate_criteria_satisfied(gate_name, fact_set, evaluated):
            # 如果当前证据已满足门控的完成准则，则将其设为关闭
            gate.status = "closed"
        elif requested_status == "closed":
            # 如果 LLM 请求关闭门控，但证据并不足以满足准则
            if not _gate_criteria_satisfied(gate_name, fact_set, evaluated):
                gate.status = prior_status or "open"  # 拒绝关闭，回退到先前状态或 open
                transitions.append(
                    {
                        "gate_name": gate_name,
                        "from_status": prior_status or "open",
                        "to_status": "open",
                        "event": "llm_close_rejected",  # 记录为 LLM 关闭请求被拒绝
                        "reason": "completion criteria not satisfied by structured evidence",
                        "evidence_facts": sorted(fact_set),
                    }
                )
        evaluated[gate_name] = gate  # 将评估后的门控存入结果字典

        current_status = getattr(gate, "status", "open")
        # 如果门控状态发生了变化，记录一次状态转换事件
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
    """
    判断当前提取到的结构化事实是否满足指定门控（gate）的闭合准则。

    不同的分析门控对事实证据的类型有不同的组合要求（例如需要反汇编指令、内存数据、
    结构体字段或符号等独立维度的交叉验证）。部分门控还存在前置门控依赖关系。

    Args:
        gate_name: 门控名称（例如 "register_provenance"、"local_corruption_exclusion" 等）。
        facts: 当前已经观测并提取出的结构化事实字符串集合（如以 "rd_word:"、"struct_" 等开头）。
        evaluated_gates: 当前已完成评估的门控字典映射，用于检查前置门控的状态。

    Returns:
        bool: 如果满足该门控的所有证据要求及前置条件则返回 True，否则返回 False。
    """
    # 统计当前事实集合中命中了哪些维度的证据类别：
    # - "rd": 内存读取证据（以 "rd_word:" 开头，记录指定地址的字值）
    # - "struct": 结构体布局证据（以 "struct_" 开头，记录结构体类型、字段偏移和大小）
    # - "dis": 反汇编证据（以 "dis_" 开头，记录指令地址、助记符和符号）
    # - "sym": 符号表证据（以 "sym:" 开头，记录符号名、地址和类型）
    categories = {
        "rd": any(fact.startswith("rd_word:") for fact in facts),
        "struct": any(fact.startswith("struct_") for fact in facts),
        "dis": any(fact.startswith("dis_") for fact in facts),
        "sym": any(fact.startswith("sym:") for fact in facts),
    }

    # 定义各门控闭合所需的证据组（每个子列表为一个要求组，组内任一类别满足即满足该组，
    # 且所有要求组均须满足）：
    # - register_provenance（故障寄存器来源）: 需内存读取证据，且需反汇编或符号证据
    # - object_lifetime（对象生命周期）: 需内存读取或结构体信息
    # - local_corruption_exclusion（局部内存破坏排除）: 需反汇编指令且需内存读取证据
    # - field_type_classification（字段类型分类）: 需结构体信息且需符号表证据
    # - external_corruption_gate（外部破坏门控）: 需至少具备一类结构化证据
    required_groups = {
        "register_provenance": [{"rd"}, {"dis", "sym"}],
        "object_lifetime": [{"rd", "struct"}],
        "local_corruption_exclusion": [{"dis"}, {"rd"}],
        "field_type_classification": [{"struct"}, {"sym"}],
        "external_corruption_gate": [{"rd", "struct", "dis", "sym"}],
    }.get(gate_name, [{"rd", "struct", "dis", "sym"}])

    # 外部破坏门控（external_corruption_gate）具有前置依赖：
    # 必须先确认局部破坏排除门控（local_corruption_exclusion）已关闭（closed）或不适用（n/a）
    if gate_name == "external_corruption_gate":
        prerequisite = evaluated_gates.get("local_corruption_exclusion")
        if prerequisite is None or getattr(prerequisite, "status", None) not in {
            "closed",
            "n/a",
        }:
            return False

    # 检查是否所有必需的证据组都得到了满足：
    # 每个 group 只要包含的类别中有至少一项为 True，则该 group 计数加 1
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
        elif name == "vtop":
            facts.update(_parse_vtop(output))
        elif name == "kmem":
            facts.update(_parse_kmem(output))
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
    """
    判断新发现的事实是否与当前的目标门控相关。

    Args:
        facts: 一个可迭代的事实字符串集合。
        goal: 当前的目标字典，通常包含 "gate_name" 键。

    Returns:
        bool: 如果至少有一个事实支持该门控，则返回 True，否则返回 False。
    """
    if not goal:  # 如果目标为空，直接返回 False
        return False
    gate_name = str(goal.get("gate_name", ""))  # 从目标中获取门控名称
    # 检查事实集合中是否至少有一个事实能支持该门控
    return any(_fact_supports_gate(fact, gate_name) for fact in facts)


def _command_lines(tool_name: str, raw_args: Any) -> list[str]:
    """
    解析工具名称和原始参数，提取出需要执行的命令行列表。

    Args:
        tool_name: 工具名称。如果是 "run_script"，则表示参数中包含多行脚本。
        raw_args: 工具的原始参数。
                - 对于直接命令工具：通常是 {"command": "..."} 或纯字符串。
                - 对于 run_script：通常是 {"script": "..."} 或包含脚本内容的字符串。

    Returns:
        list[str]: 提取出的命令行列表，每行作为一个独立的命令。
    """
    if tool_name != "run_script":  # 如果不是多行脚本模式，处理单条命令
        if isinstance(raw_args, dict):  # 如果参数是字典格式
            command = raw_args.get("command", "")  # 从字典中提取 command 字段
        else:
            command = raw_args
        command = str(command).strip()
        if not command:
            return []
        if command.split(maxsplit=1)[0].lower() != tool_name.lower():
            command = f"{tool_name} {command}"
        return [command]

    # 处理 run_script 模式下的多行脚本
    if isinstance(raw_args, dict):  # 如果参数是字典格式
        script = raw_args.get("script", "")  # 从字典中提取 script 字段
    else:  # 如果参数是字符串
        script = raw_args

    # 将脚本按行拆分，去除每行首尾空格，并过滤掉空行
    return [line.strip() for line in str(script).splitlines() if line.strip()]


def _parse_rd(output: str) -> set[str]:
    """
    解析 ``rd``（内存读取）命令的输出并提取事实（facts）。

    crash 的 ``rd`` 命令输出格式为每行一个地址后跟若干十六进制字（word），
    例如::

        ffff0000: 0000000000000010 0000000000000020  ..

    该函数将每个字与其所在地址关联，生成格式为
    ``rd_word:0x<address>=0x<value>`` 的事实。由于 64 位内核中每个字占 8 字节，
    第 index 个字的地址为 ``起始地址 + index * 8``。

    Args:
        output (str): ``rd`` 命令的标准输出字符串。

    Returns:
        set[str]: 包含提取出的内存字事实的集合。
    """
    facts: set[str] = set()
    for line in output.splitlines():
        # 匹配 "地址: 数据..." 格式的行，例如 "ffff0000: 0000000000000010 ..."
        match = _RD_LINE_RE.match(line)
        if not match:
            continue
        # 该行数据块的起始地址
        address = _to_int(match.group("address"))
        # 逐个提取十六进制字；遇到非十六进制的 token（如行尾的 ASCII 预览
        # ".." 或 "<read error>"）即停止，只保留前面有效的字
        words = []
        for token in match.group("words").split():
            if not re.fullmatch(_HEX, token):
                break
            words.append(token)
        # 每个字按其实际内存地址（起始地址 + 序号*8字节）记录一条事实，
        # 数值统一规范化为十六进制输出
        for index, word in enumerate(words):
            facts.add(f"rd_word:0x{address + index * 8:x}=0x{_to_int(word):x}")
    return facts


def _parse_struct(output: str) -> set[str]:
    """
    解析由内核调试器（如 crash）输出的结构体定义文本，并将其转换为事实（facts）集合。

    字段名与偏移的解析委托给 ``consistency.parse_struct_layouts``：crash 的
    ``struct -o`` 输出形如 ``[112] struct irqaction *action;``，字段名写在声明之后，
    不能把声明中的第一个标识符当作字段名。

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
    for type_name, layout in parse_struct_layouts(output).items():
        facts.add(f"struct_type:{type_name}")
        size = layout.get("size")
        if isinstance(size, int) and size > 0:
            facts.add(f"struct_size:{type_name}=0x{size:x}")
        for field in layout.get("fields", []):
            facts.add(
                f"struct_field:{type_name}.{field['name']}@0x{field['offset']:x}"
            )
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


def _parse_vtop(output: str) -> set[str]:
    """
    解析 ``vtop``（虚拟地址转物理地址）命令的输出并提取事实（facts）。

    该函数逐行匹配输出，识别两类映射状态信息：
    1. 地址未映射（"not mapped"）→ 生成 ``vtop_unmapped:0x<address>`` 事实，
       说明该虚拟地址在当前页表中没有对应的物理页。
    2. 页表项（PTE）值 → 生成 ``vtop_pte:0x<value>`` 事实，
       记录该地址对应的页表项内容（可用于判断页面权限、有效性等）。

    Args:
        output (str): ``vtop`` 命令的标准输出字符串。

    Returns:
        set[str]: 包含提取出的映射状态相关事实的集合。
    """
    facts: set[str] = set()
    for line in output.splitlines():
        # 尝试匹配 "未映射" 行，例如 "0xffff0000 (not mapped)"
        unmapped = _VTOP_UNMAPPED_RE.match(line)
        if unmapped:
            # 将地址转换为整数后以十六进制规范化输出
            address = _to_int(unmapped.group("address"))
            facts.add(f"vtop_unmapped:0x{address:x}")
            continue

        # 尝试匹配 PTE 行，例如 "PTE: 0x123456789" 或 "PTE: 0x123 => 0x456"
        pte = _VTOP_PTE_RE.match(line)
        if pte:
            facts.add(f"vtop_pte:0x{_to_int(pte.group('value')):x}")
    return facts


def _parse_kmem(output: str) -> set[str]:
    """
    解析 ``kmem -v``（vmalloc 信息）命令的输出并提取事实（facts）。

    该函数逐行匹配输出中的 vmalloc 虚拟内存范围记录，生成格式为
    ``kmem_vmap_range:0x<start>-0x<end>=0x<size>`` 的事实，
    用于判断某个地址是否落在已知的 vmalloc 区间内（例如判断对象是否
    来自 vmalloc 分配的内存区域）。

    Args:
        output (str): ``kmem -v`` 命令的标准输出字符串。

    Returns:
        set[str]: 包含提取出的 vmalloc 范围相关事实的集合。
    """
    facts: set[str] = set()
    for line in output.splitlines():
        match = _KMEM_RANGE_RE.match(line)
        if not match:
            continue
        # 起止地址按十六进制解析
        start = _to_int(match.group("start"))
        end = _to_int(match.group("end"))
        # 大小字段在输出中为十进制数字
        size = int(match.group("size"), 10)
        facts.add(f"kmem_vmap_range:0x{start:x}-0x{end:x}=0x{size:x}")
    return facts


def _fact_supports_gate(fact: str, gate_name: str) -> bool:
    """
    判断某个事实（fact）是否与指定的门控（gate）相关，即是否可作为该门控的证据。

    事实与门控的关联通过事实字符串的前缀来判定，不同门控接受不同类型的事实前缀：
    - register_provenance: 内存读取、反汇编、符号表、虚拟地址映射状态
    - local_corruption_exclusion: 反汇编、内存读取、映射状态、vmalloc 范围
    - field_type_classification: 结构体布局、符号表
    - object_lifetime: 内存读取、结构体布局、映射状态、vmalloc 范围
    - 其他/未知门控: 接受所有已知类型的事实

    Args:
        fact (str): 单个事实字符串（例如 "rd_word:0xffff0000=0x10"）。
        gate_name (str): 门控名称。

    Returns:
        bool: 如果该事实可作为该门控的证据则返回 True，否则返回 False。
    """
    if gate_name == "register_provenance":
        return fact.startswith(("rd_word:", "dis_", "sym:", "vtop_"))
    if gate_name == "local_corruption_exclusion":
        return fact.startswith(("dis_", "rd_word:", "vtop_", "kmem_"))
    if gate_name == "field_type_classification":
        return fact.startswith(("struct_", "sym:"))
    if gate_name == "object_lifetime":
        return fact.startswith(("rd_word:", "struct_", "vtop_", "kmem_"))
    # 兜底：未知门控接受所有已知前缀的事实
    return fact.startswith(("rd_word:", "struct_", "dis_", "sym:", "vtop_", "kmem_"))


def _to_int(value: str) -> int:
    """
    将十六进制字符串转换为整数。

    crash 输出中的地址/数值通常为十六进制（可能带 "0x" 前缀，也可能不带），
    因此无论是否带前缀都按十六进制解析。

    Args:
        value (str): 待转换的字符串，例如 "0xffff0000" 或 "ffff0000"。

    Returns:
        int: 转换后的整数值。
    """
    return int(value, 16 if value.lower().startswith("0x") else 16)
