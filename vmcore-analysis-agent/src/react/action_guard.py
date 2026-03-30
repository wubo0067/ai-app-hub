#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# action_guard.py - crash action executor 校验与规范化模块
# 本模块用于验证和规范化 crash 调试工具的命令执行请求，防止危险操作和错误用法

import re
from typing import Any, Iterable, List, Optional

# 匹配 head/tail 管道后缀的正则表达式，用于清理命令输出过滤操作
_HEAD_TAIL_SUFFIX_RE = re.compile(r"\s*\|\s*(?:head|tail)\s+-\d+\s*$")
# 禁止的表达式模式：shell 变量、寄存器引用等，防止命令注入
_FORBIDDEN_EXPR_RE = re.compile(
    r"\$\(|\$\(\(|\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z0-9]+:|\(%[A-Za-z0-9]+\)|%rip\+|\$r[A-Za-z0-9]+"
)
# 地址算术运算模式检测，要求在执行前解析地址计算
_ADDRESS_ARITHMETIC_RE = re.compile(
    r"^(?:rd|ptov|vtop|struct|sym|kmem)\b.*(?:0x[0-9A-Fa-f]+|\b[0-9A-Fa-f]{8,}\b)\+\s*(?:0x[0-9A-Fa-f]+|\d+)"
)
# 模块相关符号的前缀列表，用于检测是否需要先加载模块符号表
_MODULE_SYMBOL_PREFIXES = (
    "mlx5_",  # Mellanox 网卡驱动
    "nvme_",  # NVMe 存储驱动
    "pqi_",  # PQI 存储控制器驱动
    "qla2xxx_",  # QLogic 光纤通道驱动
    "mpt3sas_",  # LSI SAS 控制器驱动
)
# 反汇编行匹配：提取指令部分
_DISASM_LINE_RE = re.compile(r"^\s*0x[0-9a-fA-F]+\s+<[^>]+>:\s+(?P<inst>.+)$")
# 寄存器间 mov 指令匹配：追踪寄存器别名关系
_MOV_ALIAS_RE = re.compile(
    r"\bmov[a-z]*\s+%(?P<src>r(?:1[0-5]|[0-9]|[a-z]{2,3})),\s*%(?P<dst>r(?:1[0-5]|[0-9]|[a-z]{2,3}))"
)
# 内存操作数匹配：提取基址寄存器和位移
_MEMORY_OPERAND_RE = re.compile(
    r"(?:(?P<disp>0x[0-9a-fA-F]+))?\(%(?P<base>r(?:1[0-5]|[0-9]|[a-z]{2,3}))"
)
# struct 布局头部匹配：识别结构体类型名
_STRUCT_LAYOUT_HEADER_RE = re.compile(r"^struct\s+(?P<type_name>\S+)\s+\{$")
# struct 字段偏移匹配：提取字段偏移量
_STRUCT_FIELD_OFFSET_RE = re.compile(r"^\s*\[(?P<offset>\d+)\]\s+")
# struct 大小匹配：提取结构体总大小
_STRUCT_SIZE_RE = re.compile(r"^SIZE:\s+(?P<size>\d+)")


def _collapse_whitespace(text: str) -> str:
    """
    压缩空白字符：将连续的空白（空格、制表符、换行）替换为单个空格，并去除首尾空白。

    Args:
        text: 原始字符串

    Returns:
        规范化后的字符串
    """
    return " ".join(text.strip().split())


def canonicalize_command_line(command_line: str) -> str:
    """
    规范化 crash 命令行：移除输出过滤后缀、标准化选项顺序。

    主要功能：
    1. 压缩多余空白字符
    2. 移除 | head/-N 或 | tail/-N 等输出限制后缀
    3. 对 struct 命令，确保 -o 选项紧跟在 struct 之后

    Args:
        command_line: 原始 crash 命令

    Returns:
        规范化后的命令字符串
    """
    # 步骤 1: 压缩空白并去除首尾空格
    normalized = _collapse_whitespace(command_line)

    # 步骤 2: 循环移除 head/tail 后缀，直到无法再移除
    while True:
        stripped = _HEAD_TAIL_SUFFIX_RE.sub("", normalized)
        if stripped == normalized:
            break
        normalized = stripped.strip()

    # 步骤 3: 特殊处理 struct 命令，将 -o 选项移到正确位置
    parts = normalized.split()
    if parts[:1] == ["struct"] and "-o" in parts[1:]:
        # 重构为：struct -o <type> [其他参数]
        normalized = " ".join(
            ["struct", "-o", *[part for part in parts[1:] if part != "-o"]]
        )
    return normalized


def extract_command_lines(tool_name: str, args: Any) -> List[str]:
    """
    从工具调用参数中提取命令行列表。

    支持两种形式：
    1. run_script: 从 script 字段提取多行脚本
    2. 单一命令：从 command 字段或直接作为字符串参数

    Args:
        tool_name: 工具名称（如 "run_script", "crash"）
        args: 工具调用参数，可以是 dict 或字符串

    Returns:
        命令行字符串列表
    """
    # 处理 run_script 特殊情况：支持多行脚本
    if tool_name == "run_script":
        script = args.get("script", "") if isinstance(args, dict) else str(args)
        return [line.strip() for line in str(script).splitlines() if line.strip()]

    # 处理普通命令：从 dict 或字符串中提取
    if isinstance(args, dict):
        command = str(args.get("command", "")).strip()
    else:
        command = str(args).strip()
    return [command] if command else []


def build_command_fingerprint(tool_name: str, args: Any) -> str:
    """
    构建工具调用的指纹字符串，用于缓存键或日志标识。

    Args:
        tool_name: 工具名称
        args: 工具调用参数

    Returns:
        规范化的命令指纹字符串
    """
    return build_fingerprint_from_lines(extract_command_lines(tool_name, args))


def build_fingerprint_from_lines(lines: Iterable[str]) -> str:
    """
    从命令行列表构建规范化指纹。

    处理逻辑：
    1. 对每行进行规范化
    2. 跳过空行和 mod -s 命令（模块加载命令不影响指纹）

    Args:
        lines: 命令行可迭代对象

    Returns:
        由换行符连接的实质性命令字符串
    """
    substantive_lines = []
    for line in lines:
        canonical = canonicalize_command_line(line)
        # 跳过空行和模块加载命令
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
    """
    验证工具调用请求的合法性。

    检查项目：
    1. run_script 非空检查
    2. 每条命令的语法合规性
    3. run_script 不能只包含 mod -s 命令
    4. 使用模块符号时必须先加载模块
    5. struct 查询与已知偏移量的兼容性

    Args:
        tool_name: 工具名称
        args: 工具参数
        allow_bt_a: 是否允许使用 bt -a（默认禁止，除非 hard_lockup 场景）
        observed_struct_offsets: 已观察到的 struct 偏移量集合（来自反汇编分析）
        struct_layout_cache: struct 布局缓存字典

    Returns:
        若验证失败返回错误信息字符串，成功返回 None
    """
    lines = extract_command_lines(tool_name, args)

    # 检查 run_script 至少包含一条命令
    if tool_name == "run_script" and not lines:
        return "run_script must contain at least one command line."

    # 逐条验证命令合法性
    for line in lines:
        error = _validate_command_line(line, allow_bt_a=allow_bt_a)
        if error:
            return error

    # 针对 run_script 的特殊检查
    if tool_name == "run_script":
        # 提取实质性诊断命令（排除 mod -s）
        substantive_lines = [
            line
            for line in lines
            if not canonicalize_command_line(line).startswith("mod -s ")
        ]
        # 不能只包含模块加载命令
        if not substantive_lines:
            return "run_script cannot contain only mod -s; include at least one diagnostic command."

        # 如果使用模块相关符号，必须以 mod -s 开头
        if _uses_module_specific_symbol(substantive_lines):
            first_line = canonicalize_command_line(lines[0])
            if not first_line.startswith("mod -s "):
                return "run_script uses module-specific symbols/types and must start with mod -s <module> <path>."

        # 验证 struct 查询与已知偏移量的兼容性
        struct_error = _validate_struct_requests(
            substantive_lines,
            observed_struct_offsets=observed_struct_offsets,
            struct_layout_cache=struct_layout_cache,
        )
        if struct_error is not None:
            return struct_error

    return None


def _validate_command_line(command_line: str, *, allow_bt_a: bool) -> str | None:
    """
    验证单条 crash 命令的合法性。

    检查项目：
    1. struct 命令格式必须为 struct -o <type>
    2. 禁止 shell 变量和寄存器表达式
    3. 禁止未解析的地址算术运算
    4. sym -l 禁止（输出过大）
    5. bt -a 默认禁止（除非明确允许）
    6. log 命令必须配合 grep 使用
    7. kmem 必须带有效选项
    8. rd/ptov/vtop/sym 等命令必须有目标操作数

    Args:
        command_line: 待验证的命令字符串
        allow_bt_a: 是否允许 bt -a

    Returns:
        若违规返回错误信息，否则返回 None
    """
    raw_parts = _collapse_whitespace(command_line).split()

    # 检查 struct 命令格式：必须是 struct -o <type>
    if raw_parts[:1] == ["struct"] and len(raw_parts) >= 3 and "-o" in raw_parts[2:]:
        return "struct offset queries must use struct -o <type>."

    # 规范化命令以便后续检查
    normalized = canonicalize_command_line(command_line)
    if not normalized:
        return "empty crash command is not allowed."

    # 检查是否包含禁止的表达式（shell 变量、寄存器等）
    if _FORBIDDEN_EXPR_RE.search(normalized):
        return f"forbidden shell/register expression in crash command: {normalized}"

    # 检查是否包含未解析的地址算术运算
    if _ADDRESS_ARITHMETIC_RE.search(normalized):
        return f"address arithmetic must be resolved before execution: {normalized}"

    parts = normalized.split()
    command = parts[0]

    # sym -l 禁止：列出所有符号输出过大
    if command == "sym" and len(parts) > 1 and parts[1] == "-l":
        return "sym -l is forbidden; use sym <symbol>."

    # bt -a 默认禁止：除非是 hard_lockup 场景
    if command == "bt" and "-a" in parts[1:] and not allow_bt_a:
        return "bt -a is forbidden unless the current signature_class is hard_lockup."

    # log 命令检查：必须配合 grep 使用，禁止单独使用
    if command == "log":
        if normalized == "log":
            return "standalone log is forbidden; pipe it with grep."
        if normalized.startswith("log |"):
            return "log must use log -m | grep <pattern>, not log | grep."
        if len(parts) >= 2 and parts[1] in {"-m", "-t", "-a"} and "|" not in normalized:
            return f"standalone {parts[0]} {parts[1]} is forbidden; pipe it with grep."

    # kmem 命令检查：必须带有效选项
    if command == "kmem":
        if len(parts) == 1:
            return "kmem must include an option flag such as -i, -S, or -p."
        if parts[1] == "-a":
            return "kmem -a <addr> is forbidden; use kmem -S <addr>."
        if parts[1] == "-S" and len(parts) == 2:
            return "bare kmem -S is forbidden; use kmem -S <addr>."

    # struct 命令检查：禁止裸用 struct -o
    if command == "struct":
        if parts == ["struct", "-o"]:
            return "bare struct -o is forbidden; use struct -o <type>."

    # rd 命令检查：必须提供地址目标
    if command == "rd":
        if all(part.startswith("-") for part in parts[1:]):
            return "rd command is incomplete; provide an address target."

    # ptov/vtop/sym 命令检查：必须有目标操作数
    if command in {"ptov", "vtop", "sym"} and len(parts) == 1:
        return f"{command} requires a target operand."

    return None


def _uses_module_specific_symbol(lines: Iterable[str]) -> bool:
    """
    检测命令行是否使用了模块相关的符号或类型。

    通过检查命令中是否包含已知驱动模块的符号前缀来判断。

    Args:
        lines: 命令行列表

    Returns:
        若使用模块符号返回 True，否则返回 False
    """
    for line in lines:
        lowered = canonicalize_command_line(line).lower()
        # 检查是否包含任何模块符号前缀
        if any(prefix in lowered for prefix in _MODULE_SYMBOL_PREFIXES):
            return True
    return False


def extract_crash_path_struct_offsets(tool_output: str) -> list[int]:
    """
    从 crash 反汇编输出中提取 per-CPU 路径的 struct 偏移量。

    算法流程：
    1. 解析反汇编指令，追踪寄存器别名关系（mov 指令）
    2. 提取内存访问指令中的位移值（如 mov %gs:0x14168, %rax 中的 0x14168）
    3. 过滤掉栈帧相关寄存器（rsp/rbp/rip）
    4. 找到被访问最多的主导寄存器（dominant root）
    5. 返回该寄存器的所有偏移量（排序后）

    Args:
        tool_output: crash 工具的反汇编输出文本

    Returns:
        偏移量整数列表（已排序）
    """
    alias_parent: dict[str, str] = {}  # 寄存器别名映射：子 -> 父
    offsets_by_root: dict[str, set[int]] = {}  # 每个根寄存器的偏移集合

    # 查找寄存器的根节点（带路径压缩）
    def find(reg: str) -> str:
        parent = alias_parent.get(reg, reg)
        while parent != alias_parent.get(parent, parent):
            parent = alias_parent.get(parent, parent)
        alias_parent[reg] = parent  # 路径压缩
        return parent

    # 逐行解析反汇编输出
    for line in tool_output.splitlines():
        match = _DISASM_LINE_RE.match(line)
        if match is None:
            continue

        instruction = match.group("inst")

        # 检查是否为寄存器间 mov 指令，建立别名关系
        alias_match = _MOV_ALIAS_RE.search(instruction)
        if alias_match is not None:
            src = find(alias_match.group("src"))
            dst = alias_match.group("dst")
            alias_parent[dst] = src  # dst 成为 src 的别名

        # 提取内存操作数中的偏移量
        for mem_match in _MEMORY_OPERAND_RE.finditer(instruction):
            base = find(mem_match.group("base"))
            # 跳过栈帧和指令指针相关寄存器
            if base in {"rsp", "rbp", "rip"}:
                continue
            displacement = mem_match.group("disp")
            # 位移可能为空（如 mov (%rax), %rbx），此时偏移为 0
            offset = int(displacement, 16) if displacement is not None else 0
            offsets_by_root.setdefault(base, set()).add(offset)

    # 如果没有有效的内存访问，返回空列表
    if not offsets_by_root:
        return []

    # 选择主导寄存器：访问次数最多且最大偏移最大的寄存器
    dominant_root = max(
        offsets_by_root,
        key=lambda reg: (len(offsets_by_root[reg]), max(offsets_by_root[reg])),
    )
    return sorted(offsets_by_root[dominant_root])


def extract_struct_layouts(tool_output: str) -> dict[str, dict[str, Any]]:
    """
    从 crash struct 命令输出中解析结构体布局信息。

    解析格式示例：
    ```
    struct task_struct {
        [0] pid
        [8] state
        ...
        SIZE: 1024
    }
    ```

    Args:
        tool_output: crash struct 命令的输出文本

    Returns:
        字典：{结构体类型名：{"size": 大小，"field_offsets": [字段偏移列表]}}
    """
    layouts: dict[str, dict[str, Any]] = {}
    current_type: Optional[str] = None  # 当前正在解析的结构体类型
    current_offsets: list[int] = []  # 当前结构体的字段偏移列表

    # 逐行解析
    for raw_line in tool_output.splitlines():
        line = raw_line.strip()

        # 检查是否为结构体头部
        header_match = _STRUCT_LAYOUT_HEADER_RE.match(line)
        if header_match is not None:
            current_type = header_match.group("type_name")
            current_offsets = []
            continue

        # 如果还没有遇到结构体头部，跳过
        if current_type is None:
            continue

        # 检查是否为字段行（格式：[offset] field_name）
        field_match = _STRUCT_FIELD_OFFSET_RE.match(raw_line)
        if field_match is not None:
            current_offsets.append(int(field_match.group("offset")))
            continue

        # 检查是否为结构体尾部（SIZE: xxx）
        size_match = _STRUCT_SIZE_RE.match(line)
        if size_match is not None:
            # 保存解析结果
            layouts[current_type] = {
                "size": int(size_match.group("size")),
                "field_offsets": sorted(set(current_offsets)),  # 去重并排序
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
    """
    验证 struct 查询请求与已知偏移量的兼容性。

    验证逻辑：
    1. 如果已知某些偏移量（来自反汇编分析），检查 struct 查询是否能覆盖这些偏移
    2. 禁止同时使用 struct -o <type> 和 struct <type> <addr>（应先检查布局再查实例）
    3. 检查结构体大小是否足够大以包含所有已知偏移

    Args:
        lines: 待验证的命令列表
        observed_struct_offsets: 已观察到的偏移量集合
        struct_layout_cache: struct 布局缓存

    Returns:
        若不兼容返回错误信息，否则返回 None
    """
    # 收集所有已观察到的偏移量（去重排序）
    observed_offsets = sorted({int(offset) for offset in observed_struct_offsets or []})
    if not observed_offsets:
        return None  # 没有已知偏移，无需验证

    layout_cache = struct_layout_cache or {}
    offset_query_types: set[str] = set()  # 使用 struct -o 查询的类型
    instance_query_types: list[str] = []  # 使用 struct <type> <addr> 查询的类型

    # 分类统计 struct 查询
    for line in lines:
        parts = canonicalize_command_line(line).split()
        if len(parts) < 2 or parts[0] != "struct":
            continue
        # struct -o <type> 形式：查询布局
        if len(parts) >= 3 and parts[1] == "-o":
            offset_query_types.add(parts[2])
            continue
        # struct <type> <addr> 形式：查询实例
        if parts[1] != "-o" and len(parts) >= 3:
            instance_query_types.append(parts[1])

    # 验证每个实例查询
    for type_name in instance_query_types:
        cached_layout = layout_cache.get(type_name)

        # 如果缓存中没有布局，且同时在进行 -o 查询，报错
        if cached_layout is None and type_name in offset_query_types:
            return (
                f"struct {type_name} <addr> cannot be combined with first-time struct -o {type_name} "
                "when crash-path offsets are already known; inspect the layout in one step, then issue "
                "struct <type> <addr> only after validating offset coverage."
            )

        # 如果缓存中没有布局，跳过后续检查
        if cached_layout is None:
            continue

        # 检查结构体大小是否足够
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

        # 检查是否有未覆盖的偏移量
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
