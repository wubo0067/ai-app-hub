"""证据值级一致性派生：把结构化证据中的字段值与类型语义做机械交叉检查。

本模块只做机械推导，不做诊断推断：

- 把 ``struct -o <type>`` 的布局输出解析为 ``{类型名: {"size": int, "fields": [...]}}``；
- 把 ``rd -x <address> <count>`` 的输出解析为 ``MemoryRead(address, words)``，
  其中 ``words`` 按 crash 约定为连续的 8 字节字；
- 用布局判定"某段已读内存的内容是否符合该类型的指针字段语义"。

产出只有一类字符串事实：``conflict:object_does_not_match_type:...``。
它表达的是"这段内存的内容与声明类型不相容"这一可复核的观察，而不是任何根因结论；
本模块既不产生根因，也不参与门控（gate）闭合判定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

_HEX = r"(?:0x)?[0-9a-fA-F]+"

# 结构体布局头部，例如 "struct irq_desc {"。
_STRUCT_HEADER_RE = re.compile(r"^\s*struct\s+(?P<name>[A-Za-z_]\w*)\s*\{\s*$")
# 带偏移的字段行，例如 "  [112] struct irqaction *action;"。
# crash 的 struct -o 输出中偏移是十进制，字段名可能跟在声明之后，
# 也可能只有字段名（简化格式 "[0] handler"）。
_STRUCT_FIELD_RE = re.compile(rf"^\s*\[(?P<offset>{_HEX})\]\s+(?P<decl>.*?)\s*;?\s*$")
_STRUCT_SIZE_RE = re.compile(rf"^\s*SIZE\s*:\s*(?P<size>{_HEX})")
# 布局块结束行。嵌套命名结构/匿名 union 会以 "} name;" 形式出现，
# 因此只要求行首为 "}"。
_STRUCT_CLOSE_RE = re.compile(r"^\s*\}")
# 函数指针字段声明，例如 "int (*irq_set_type)(struct irq_data *, unsigned int);"。
_FN_POINTER_DECL_RE = re.compile(r"\(\s*\*\s*(?P<name>[A-Za-z_]\w*)\s*\)")
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")
_ARRAY_SUFFIX_RE = re.compile(r"\[[^\]]*\]\s*$")
_BITFIELD_SUFFIX_RE = re.compile(r":\s*\d+\s*$")
_RD_LINE_RE = re.compile(rf"^\s*(?P<address>{_HEX})\s*:\s*(?P<words>.*)$")
_RD_WORD_RE = re.compile(r"^[0-9a-fA-F]{1,16}$")
_CONFLICT_FACT_RE = re.compile(
    r"^conflict:object_does_not_match_type:(?P<type>[A-Za-z_]\w*)@0x(?P<base>[0-9a-fA-F]+):"
    r"(?P<detail>.+)$"
)

# 声明中不参与"类型"判断的词。
_TYPE_QUALIFIERS = frozenset({"const", "volatile", "struct", "union", "enum"})

# 已在证据布局中确认过的函数指针 typedef：这些名字不带 "*"，但语义上是指针。
# 未列入的 typedef（如 atomic_t / cpumask_var_t）按非指针处理，
# 宁可漏报也不把"小的计数值"误判为非法指针。
_FUNCTION_POINTER_TYPEDEFS = frozenset({"irq_handler_t", "irq_flow_handler_t"})

# crash 的 rd -x 以 8 字节为一个字。
_WORD_SIZE = 8

# NULL 页区域上限：低于该值且非零的指针字段值被认为是非法值。
# 与 output_parser 的故障地址判据保持同一语义。
DEFAULT_NULL_PAGE_LIMIT = 0x10000

# 判定"内容与声明类型不相容"所需的最少违规指针字段数。
# 取 2 是为了让单个字段的合法小值（例如合法的 pd 值）不足以触发矛盾。
_MIN_OFFENDING_POINTER_FIELDS = 2

# 单条矛盾事实中最多列出的违规字段数（控制提示词长度）。
_MAX_LISTED_FIELDS = 4


@dataclass(frozen=True)
class MemoryRead:
    """一次 ``rd`` 读取得到的连续内存快照。"""

    address: int
    words: tuple[int, ...]


def _to_offset(text: str) -> int | None:
    """解析 struct -o 的字段偏移（十进制，允许 0x 前缀）。"""
    token = text.strip()
    if not token:
        return None
    try:
        return int(token, 0) if token.lower().startswith("0x") else int(token, 10)
    except ValueError:
        return None


def _to_address(text: str) -> int | None:
    """解析 crash 输出中的地址（裸十六进制，允许 0x 前缀）。"""
    token = text.strip()
    if not token:
        return None
    try:
        return int(token, 0) if token.lower().startswith("0x") else int(token, 16)
    except ValueError:
        return None


def _parse_field(offset_text: str, declaration_text: str) -> dict[str, Any] | None:
    """把一行字段声明解析为 ``{offset, name, declaration, pointer, void_pointer}``。"""
    offset = _to_offset(offset_text)
    if offset is None or offset < 0:
        return None

    declaration = _BITFIELD_SUFFIX_RE.sub("", declaration_text.strip()).strip()
    if not declaration:
        return None

    # 函数指针字段的字段名写在 "(*name)" 里，不能按"最后一个标识符"取，
    # 否则会把参数类型名当成字段名。
    function_pointer = _FN_POINTER_DECL_RE.search(declaration)
    if function_pointer is not None:
        return {
            "offset": offset,
            "name": function_pointer.group("name"),
            "declaration": declaration,
            "pointer": True,
            "void_pointer": False,
        }

    without_array = _ARRAY_SUFFIX_RE.sub("", declaration).strip()
    identifiers = _IDENT_RE.findall(without_array)
    if not identifiers:
        return None

    type_tokens = [token for token in identifiers[:-1] if token not in _TYPE_QUALIFIERS]
    pointer = "*" in without_array or any(
        token in _FUNCTION_POINTER_TYPEDEFS for token in type_tokens
    )
    return {
        "offset": offset,
        "name": identifiers[-1],
        "declaration": declaration,
        "pointer": pointer,
        # void * 常被用作不透明的 cookie（dev_id 之类），不参与判定。
        "void_pointer": pointer and type_tokens == ["void"],
    }


def parse_struct_layouts(text: str) -> dict[str, dict[str, Any]]:
    """解析 crash ``struct`` 输出中的结构体布局。

    只处理带 ``[<offset>]`` 的布局块（``struct -o`` 的风格）；不带偏移的实例转储
    （``struct task_struct <addr>`` 输出 ``field = value,``）不会被解析。

    Returns:
        ``{类型名: {"size": int | None, "fields": [字段字典, ...]}}``。
        同一类型多次出现时保留字段更多的那一份；被截断的布局块也会被保留，
        以免丢失已观测到的字段。
    """
    layouts: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    depth = 0
    # 布局块在 "}" 处结束，但 "SIZE: <n>" 行紧随其后，因此关闭后仍需保留
    # current 以便 SIZE 行能挂到同一份布局上。
    closed = False

    def _commit(layout: dict[str, Any] | None) -> None:
        if layout is None or not layout["fields"]:
            return
        existing = layouts.get(layout["name"])
        if existing is None or len(layout["fields"]) > len(existing["fields"]):
            layouts[layout["name"]] = layout

    for line in text.splitlines():
        header = _STRUCT_HEADER_RE.match(line)
        if header:
            _commit(current)
            current = {"name": header.group("name"), "size": None, "fields": []}
            depth = 1
            closed = False
            continue

        size = _STRUCT_SIZE_RE.match(line)
        if size is not None and current is not None:
            current["size"] = _to_offset(size.group("size"))
            _commit(current)
            current = None
            depth = 0
            closed = False
            continue

        if current is None or closed:
            continue

        if _STRUCT_CLOSE_RE.match(line):
            depth -= 1
            if depth <= 0:
                _commit(current)
                closed = True
                depth = 0
            continue

        # 匿名 union/struct 的头部行只增加嵌套深度，不构成字段。
        if "{" in line:
            depth += 1
            continue

        field_match = _STRUCT_FIELD_RE.match(line)
        if field_match is None:
            continue
        parsed = _parse_field(field_match.group("offset"), field_match.group("decl"))
        if parsed is not None:
            current["fields"].append(parsed)

    _commit(current)
    return layouts


def parse_memory_reads(text: str) -> list[MemoryRead]:
    """解析 crash ``rd -x`` 输出为内存快照列表。

    crash 按行打印内存，每行通常是 2 个字，因此连续的地址行会被合并为一次
    快照，才能与结构体布局的字节数进行比较。合并只依据地址连续性，
    两次分别发起的读取不会因此被错误拼接（起始地址不连续即断开）。
    """
    snapshots: list[MemoryRead] = []
    start: int | None = None
    words: list[int] = []

    def _flush() -> None:
        if start is not None and words:
            snapshots.append(MemoryRead(address=start, words=tuple(words)))

    for line in text.splitlines():
        match = _RD_LINE_RE.match(line)
        if match is None:
            continue
        address = _to_address(match.group("address"))
        if address is None:
            continue
        line_words: list[int] = []
        for token in match.group("words").split():
            # 行尾的 ASCII 列与缩进说明不属于字值，遇到即停止。
            if _RD_WORD_RE.match(token) is None:
                break
            line_words.append(int(token, 16))
        if not line_words:
            continue

        if start is not None and address == start + len(words) * _WORD_SIZE:
            words.extend(line_words)
            continue

        _flush()
        start = address
        words = list(line_words)

    _flush()
    return snapshots


def _pointer_fields_by_offset(layout: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    """按偏移归组字段，并丢弃无法确定语义的偏移（同一偏移多个字段 = union）。"""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for field in layout.get("fields", []):
        if not isinstance(field, dict):
            continue
        offset = field.get("offset")
        if not isinstance(offset, int) or offset < 0:
            continue
        grouped.setdefault(offset, []).append(field)

    resolved: dict[int, dict[str, Any]] = {}
    for offset, fields in grouped.items():
        if len(fields) != 1:
            continue
        field = fields[0]
        if not field.get("pointer") or field.get("void_pointer"):
            continue
        resolved[offset] = field
    return resolved


def detect_value_conflicts(
    reads: Iterable[MemoryRead],
    layouts: Mapping[str, Mapping[str, Any]],
    *,
    null_page_limit: int = DEFAULT_NULL_PAGE_LIMIT,
) -> list[str]:
    """判定已读内存内容是否与某个结构体类型的指针字段语义相容。

    判据只有一条：当一次读取覆盖了某个类型的完整布局，而该布局中至少
    ``_MIN_OFFENDING_POINTER_FIELDS`` 个非 void 指针字段落在 NULL 页区域内
    且非零时，输出一条 ``conflict:object_does_not_match_type:...`` 事实。

    该判据不使用页表宽度或规范地址形式，因此对 LA48/LA57、vmalloc、percpu
    等不同地址空间的转储都成立；代价是只能发现"落入 NULL 页区域的非法指针值"，
    不会对"指向别处的合法地址"做出判断。

    Args:
        reads: 已解析的 ``rd`` 内存快照。
        layouts: 结构体布局（``parse_struct_layouts`` /
            ``action_guard.extract_struct_layouts`` 的结果，可跨步骤累积）。
            键为类型名；条目里的 ``name`` 缺省时回退到键名。
        null_page_limit: NULL 页区域上限。

    Returns:
        按发现顺序去重后的矛盾事实列表。
    """
    conflicts: list[str] = []
    seen: set[str] = set()
    read_list = list(reads)

    for type_name, layout in layouts.items():
        size = layout.get("size")
        if not isinstance(size, int) or size <= 0:
            # 没有 SIZE 的布局无法判断"读取是否覆盖了完整对象"。
            continue

        pointer_fields = _pointer_fields_by_offset(layout)
        if len(pointer_fields) < _MIN_OFFENDING_POINTER_FIELDS:
            continue

        for read in read_list:
            coverage = len(read.words) * _WORD_SIZE
            if coverage < size:
                continue

            offending: list[tuple[int, dict[str, Any], int]] = []
            for offset in sorted(pointer_fields):
                if offset % _WORD_SIZE:
                    continue
                index = offset // _WORD_SIZE
                if index >= len(read.words):
                    continue
                value = read.words[index]
                if 0 < value < null_page_limit:
                    offending.append((offset, pointer_fields[offset], value))

            if len(offending) < _MIN_OFFENDING_POINTER_FIELDS:
                continue

            details = ",".join(
                f"{field.get('name', '?')}@0x{offset:x}=0x{value:x}"
                for offset, field, value in offending[:_MAX_LISTED_FIELDS]
            )
            fact = (
                f"conflict:object_does_not_match_type:{layout.get('name') or type_name}"
                f"@0x{read.address:x}:{details}"
            )
            if fact not in seen:
                seen.add(fact)
                conflicts.append(fact)

    return conflicts


def parse_conflict_fact(fact: str) -> tuple[str, int] | None:
    """从矛盾事实中取出 ``(类型名, 对象基址)``，格式不符时返回 None。"""
    if not isinstance(fact, str):
        return None
    match = _CONFLICT_FACT_RE.match(fact)
    if match is None:
        return None
    return match.group("type"), int(match.group("base"), 16)


def format_conflict_fact(fact: str) -> str | None:
    """把矛盾事实渲染为可读句子（供提示词展示），格式不符时返回 None。"""
    if not isinstance(fact, str):
        return None
    match = _CONFLICT_FACT_RE.match(fact)
    if match is None:
        return None
    fields = ", ".join(match.group("detail").split(","))
    type_name = match.group("type")
    return (
        f"{type_name} @ 0x{match.group('base')} does not look like a valid {type_name}: "
        f"pointer field(s) hold non-pointer values ({fields})"
    )


# 模型显式核对某条冲突时应写出的引导短语（与 prompt_builder 的提示词保持一致）。
# 分析文本里出现"短语 + 对象标识"即视为该冲突已被回应，不再计入未解决冲突。
RESOLVED_CONFLICT_MARKER = "已核对冲突"


def conflict_resolution_phrase(fact: str) -> str | None:
    """返回模型回应这条冲突时应写出的短语；事实不可解析时返回 None。"""
    parsed = parse_conflict_fact(fact)
    if parsed is None:
        return None
    type_name, base = parsed
    return f"{RESOLVED_CONFLICT_MARKER}{type_name}@0x{base:x}"


def is_conflict_resolved(fact: str, analysis_text: str) -> bool:
    """判断分析文本是否显式回应了这条冲突（忽略大小写与空白）。"""
    phrase = conflict_resolution_phrase(fact)
    if not phrase or not analysis_text:
        return False
    normalized = re.sub(r"\s+", "", analysis_text.lower())
    return re.sub(r"\s+", "", phrase.lower()) in normalized


def prune_resolved_value_conflicts(
    facts: Iterable[str], analysis_text: str
) -> list[str]:
    """剔除模型本次分析已显式核对过的冲突，保持原有顺序。

    冲突要求的是"回应"而不是"永久否决"：模型给出替代解释后应从未解决列表消失。
    否则追加式的 ``value_conflicts`` 会在剩余步数里持续把根因压回 unknown，agent
    既无法收敛也无法继续取证。无法解析的事实一律保留。
    """
    return [fact for fact in facts if not is_conflict_resolved(fact, analysis_text)]
