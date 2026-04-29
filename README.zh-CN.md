README.md
# VMCore Analysis Agent

[🇺🇸 English](./README.md)

一个基于 LangGraph ReAct 模式与 MCP 工具的智能 Linux 内核崩溃（vmcore）分析代理。

## 项目介绍

### Linux Kernel Crash 定位分析

**VMCore Analysis Agent** 是一个生产级的自动化 Linux 内核崩溃（vmcore）诊断平台。它通过将 **LangGraph ReAct** 模式与 **MCP (Model Context Protocol)** 工具体系相结合，将原本高度依赖人工经验的复杂内核调试过程转化为可自动执行的 AI 工作流。

### 核心技术亮点

- **分层专家知识体系**：不同于简单的 RAG，本项目实现了**三层动态 Prompt 注入架构**（全局基座、场景剧本、SOP 片段）。系统根据崩溃特征（如 Soft Lockup 或内存损坏）按需加载诊断逻辑，显著降低 Token 噪音并提升推理精度。
- **证据驱动的状态机管理**：代理维护着包含**活跃假设 (Hypotheses)** 与 **验证门控 (Gates)** 的结构化状态。强制要求 LLM 在得出结论前必须收集特定的诊断证据（如寄存器来源追踪、堆栈回溯），确保分析过程的严谨性与可追溯性。
- **基于 MCP 的深度工具集成**：利用模型上下文协议 (MCP)，代理实现了与 Linux `crash` 工具的高保真连接。AI 不再是“盲目猜测”，而是根据推理路径动态地探索内存、反汇编代码并检索内核对象状态。
- **执行器级安全防护**：内置 `action_guard` 模块，防止 LLM 执行资源消耗过大或高风险的命令（如在大系统上盲目执行 `bt -a`），同时通过命令去重机制确保分析效率，防止推理陷入死循环。
- **透明的思考链报告**：每一次分析都会生成结构化的 Markdown 报告，完整记录每一步命令的执行意图、假设的验证过程以及基于证据的最终根因定界。

## 架构设计

### 整体架构图

```
graph TB
    subgraph Client
        A[client.py] -->|HTTP / SSE| B
    end

    subgraph "FastAPI Server"
        B["POST /analyze\nGET /analyze/stream"] --> C[create_agent_graph]
        B --> D[generate_markdown_report]
    end

    subgraph "LangGraph ReAct Agent"
        C --> E["collect_crash_init_data_node\nsys / sys -t / bt"]
        E -->|HumanMessage| F{should_continue}
        F -->|初始数据就绪 | G["llm_analysis_node\nDeepSeek-Reasoner"]
        G -->|AIMessage with tool_calls| H{should_continue}
        H -->|需要工具 | I[crash_tool_node]
        H -->|reasoning_to_structure| J["structure_reasoning_node\ndeepseek-chat"]
        H -->|分析完毕 | K[__end__]
        I -->|after_crash_tool| G
        J -->|结构化后 | H
    end

    subgraph "MCP Tools"
        I -->|crash 命令 | L["crash MCP Server\nmcp_tools/crash/server.py"]
        I -->|源码补丁 | M["source_patch MCP Server\nmcp_tools/source_patch"]
        L --> N["crash utility\nvmcore + vmlinux"]
        M --> O[unified diff patch]
    end

    K --> D
    D -->|Markdown 报告 | P[reports]
```

**架构图说明**：
- **实线箭头** 表示数据流或调用关系
- **花括号节点**（如 `{should_continue}`）表示条件路由判断
- **方括号节点** 表示具体的功能节点或外部服务
- 流程从 `START` 开始，经过初始化数据收集，进入 LLM 分析与工具调用的循环，直到 `is_conclusive=true` 或达到递归限制时结束

## Vmcore Analysis React Agent

### 代理架构

基于 LangGraph 的 ReAct（推理 - 行动）代理，包含四个核心节点：

| 节点 | 说明 |
|------|------|
| `collect_crash_init_data_node` | 初始节点，并发执行 `sys`、`bt` 等命令收集 vmcore 基础信息 |
| `llm_analysis_node` | 调用 DeepSeek-Reasoner，输出结构化 `VMCoreAnalysisStep`，决定下一步行动 |
| `crash_tool_node` | 解析 LLM 工具调用请求，通过 MCP 并发执行 crash 命令 |
| `structure_reasoning_node` | 当 Reasoner 返回纯文本 `reasoning_content` 时，用 deepseek-chat 将其结构化（可选） |

### 节点流转图

```
stateDiagram-v2
    [*] --> collect_crash_init_data_node
    collect_crash_init_data_node --> llm_analysis_node : HumanMessage（基础信息）

    state llm_analysis_node {
        [*] --> DeepSeek_Reasoner
        DeepSeek_Reasoner --> 有tool_calls : 需要更多数据
        DeepSeek_Reasoner --> reasoning_to_structure : Reasoner 纯文本输出
        DeepSeek_Reasoner --> 分析完毕 : is_conclusive = true
    }

    llm_analysis_node --> crash_tool_node : 有 tool_calls
    llm_analysis_node --> structure_reasoning_node : reasoning_to_structure
    llm_analysis_node --> [*] : 分析完毕

    crash_tool_node --> llm_analysis_node : ToolMessage（命令结果）
    crash_tool_node --> [*] : is_last_step

    structure_reasoning_node --> llm_analysis_node : 结构化 AIMessage
```

### 详细节点说明

#### 节点 1：collect_crash_init_data_node

**功能**：执行默认的 crash 命令集合，收集 vmcore 基础信息

**默认命令**：
```
DEFAULT_CRASH_COMMANDS = [
    "sys",  # 系统信息（内核版本、崩溃时间等）
    "bt",   # 崩溃现场的堆栈回溯
]
```

执行结果以 `HumanMessage` 形式写入消息队列，作为 LLM 分析的初始上下文。

#### 节点 2：llm_analysis_node

**功能**：调用 DeepSeek-Reasoner，按 ReAct 模式输出结构化分析步骤

**核心实现逻辑**（[`call_llm_analysis`](vmcore-analysis-agent/src/react/llm_node.py#L27-L212) 函数）：

1. **消息压缩**：通过 [`compress_messages_for_llm`](vmcore-analysis-agent/src/react/llm_runtime.py#L104-L134) 压缩历史消息，避免 reasoning_content 累积导致 token 暴增
2. **系统提示构建**：使用动态分层注入架构（[`prompt_builder.py`](vmcore-analysis-agent/src/react/prompt_builder.py)），包含：
   - 角色定义：资深 Linux Kernel Crash Dump 分析专家
   - 输出契约：严格遵循 `VMCoreLLMAnalysisStep` JSON Schema
   - 最后一步强制约束：当 `is_last_step=True` 时，必须给出结论且禁止工具调用
3. **结构化输出**：使用 `llm_with_tools.with_structured_output(VMCoreLLMAnalysisStep, method="json_mode", include_raw=True)`
4. **容错处理**：
   - JSON 格式错误时尝试修复（[`repair_structured_output`](vmcore-analysis-agent/src/react/output_parser.py#L56-L94)）
   - 纯文本 `reasoning_content` 路由到 [`structure_reasoning_node`](vmcore-analysis-agent/src/react/llm_node.py#L215-L350)
   - 空响应时注入 HumanMessage 强制 LLM 行动或得出结论
5. **状态管理**：通过 [`project_managed_analysis_step`](vmcore-analysis-agent/src/react/state_manager.py#L123-L198) 将 LLM 输出与托管状态（hypotheses、gates）合并

**核心 Prompt 设计**：
- 内置防止重复执行同一命令的规则（Anti-Repetition Policy）
- 禁止触发 token 溢出的高危命令（`sym -l`、`bt -a`、`ps -m` 等）
- 支持 `run_script` 批量执行，确保模块符号在同一 crash session 内加载
- 强调基于诊断证据的推理，禁止无证据猜测

**System Prompt 分层注入架构**：
代理实现了一个精密的三层动态 Prompt 注入系统，体现了生产级 Agent 架构中**"指令按需加载"**的核心原则：

- **Layer 0: 全局基座 (Global Base)**
  - 由 `LAYER0_SYSTEM_PROMPT_TEMPLATE` 组成
  - 定义了 Agent 的身份（Role）、核心禁止事项和输出契约
  - 作为不可变的"宪法"，在所有分析阶段始终保持激活状态

- **Layer 1: 场景剧本 (Scenario Playbooks)**
  - 通过 `_select_playbook` 根据 `current_signature_class` 动态选择
  - 实现指令隔离：分析 `null_deref` 时，LLM 完全接触不到 `lockup` 或 `rcu_stall` 的复杂逻辑
  - 消除干扰项，有效解决复杂分析中的注意力下降问题

- **Layer 2: 动态片段 (Dynamic SOP Fragments)**
  - 通过 `_select_sop_fragments` 根据步数、关键字和 Gate 状态动态注入
  - 采用**上下文触发逻辑**：仅在满足相关条件时才显示 SOP 片段
  - 示例：`per-cpu` SOP 仅在最近消息中出现 "%gs" 或 "per-cpu" 时注入；`dma_corruption` SOP 仅在外存损坏门控开启时激活

**实现亮点**：
- **状态驱动注入**：利用 `managed_gates` 状态有条件地注入专业 SOP，避免在无证据时进行推测性分析
- **智能去重**：`_dedupe_preserve_order` 确保即使多个触发条件同时激活同一 SOP，生成的 Prompt 依然简洁无冗余
- **动态执行器状态**：`build_executor_state_section` 为 LLM 提供清晰的"任务地图"，显示当前假设、门控状态和近期命令
- **Token 优化**：相比静态完整 Prompt，系统 Prompt 的 token 消耗减少 40-70%，同时保持全面的知识覆盖

**输出 Schema**（[`VMCoreLLMAnalysisStep`](vmcore-analysis-agent/src/react/schema.py#L70-L114)）：
```json
{
  "step_id": 1,
  "reasoning": "3-6 句结构化分析总结： (1) 从最新工具输出中学到什么？(2) 如何更新假设？(3) 下一步最诊断性的行动及原因",
  "action": { "command_name": "bt", "arguments": ["-c", "3"] },
  "is_conclusive": false,
  "signature_class": "soft_lockup",
  "root_cause_class": null,
  "partial_dump": "full",
  "active_hypotheses": null,  // 由 executor 维护，LLM 无需输出
  "gates": null,              // 由 executor 维护，LLM 无需输出
  "final_diagnosis": null,
  "fix_suggestion": null,
  "confidence": null,
  "additional_notes": null
}
```

**Token 管理**：
- 记录每次调用的 token 消耗（`usage_metadata`）
- 在状态中累积 `token_usage`，用于监控和优化

#### 节点 3：crash_tool_node

**功能**：接收 LLM 的工具调用请求，通过 MCP 并发执行 crash 命令

**技术实现**：
- 使用 `langchain-mcp-adapters` 加载 MCP 工具
- 通过 `asyncio.gather` 并发执行多条命令，提升效率
- 每次调用独立管理 MCP 会话生命周期
- 安全封装工具调用，捕获异常并以错误字符串返回，不中断流程

**支持的 MCP 工具服务**：
- **crash MCP Server**：提供 `run_script` 及各 crash 子命令（`bt`、`dis`、`struct`、`kmem`、`sym` 等）
- **source_patch MCP Server**：根据 LLM 的源码分析结论生成 unified diff patch 文件

#### 节点 4：structure_reasoning_node（可选）

**功能**：DeepSeek-Reasoner 有时仅返回 `reasoning_content` 纯文本而 `content` 为空，此节点使用 deepseek-chat 将其转化为合法的 `VMCoreAnalysisStep` JSON，保证图的正常流转。

### 核心数据模型 (Core Data Models)

代理的推理和状态围绕 `src/react/schema.py` 中定义的一组 Pydantic 模型进行结构化。这些模型确保了 LLM 与执行器之间严格、类型安全的通信，并强制执行一种有纪律的分析过程。

| 模型 | 说明 |
|---|---|
| [`VMCoreAnalysisStep`](vmcore-analysis-agent/src/react/schema.py#L230-L373) | 代表分析中单个步骤的主要数据结构。它包含 LLM 的推理、请求的操作（[`ToolCall`](vmcore-analysis-agent/src/react/schema.py#L11-L34)），以及至关重要的托管状态：[`active_hypotheses`](vmcore-analysis-agent/src/react/schema.py#L326-L333)（活跃假设）和 [`gates`](vmcore-analysis-agent/src/react/schema.py#L335-L343)（验证门）。 |
| [`VMCoreLLMAnalysisStep`](vmcore-analysis-agent/src/react/schema.py#L70-L114) | [`VMCoreAnalysisStep`](vmcore-analysis-agent/src/react/schema.py#L230-L373) 的一个最小子集，是 LLM 直接输出的内容。执行器会为其补充托管状态字段。 |
| [`FinalDiagnosis`](vmcore-analysis-agent/src/react/schema.py#L45-L67) | 最终结论的完整记录，仅在 [`is_conclusive`](vmcore-analysis-agent/src/react/schema.py#L294-L294) 为 `true` 时填充。 |
| [`Hypothesis`](vmcore-analysis-agent/src/react/schema.py#L165-L192) | 代表代理正在跟踪的一个候选根本原因。[`active_hypotheses`](vmcore-analysis-agent/src/react/schema.py#L326-L333) 列表强制对竞争性理论进行显式管理。 |
| [`GateEntry`](vmcore-analysis-agent/src/react/schema.py#L195-L220) | 代表一个强制性的验证检查点。[`gates`](vmcore-analysis-agent/src/react/schema.py#L335-L343) 字典确保在允许得出确定性诊断之前，收集到所有必需的证据。 |

#### `_REQUIRED_GATES`：验证门控系统

**一、Gates 是什么**

Gates（门控/检查点）是该项目中 VMCore 崩溃分析状态机的一个核心概念——它代表在宣布分析"已得出结论"（is_conclusive=True）之前，必须完成的验证步骤（检查点）。

可以从代码中 GateEntry 的定义清楚看到这一点：

```python
class GateEntry(BaseModel):
    """
    is_conclusive=True 前必须完成的验证检查点。

    每个 gate 代表一个必须完成的验证步骤，用于确认崩溃根因的特定假设。
    在将分析结论标记为"最终结论"（is_conclusive=True）之前，所有相关的 gate
    必须处于 closed 或 n/a 状态，且 evidence 字段必须填写具体的工具输出。
    """
    status: Literal["open", "closed", "blocked", "n/a"]
    evidence: Optional[str]  # 必须填写具体的工具输出，不得使用泛泛总结
    prerequisite: Optional[str]  # 前置依赖 gate
```

Gate 的四种状态：

| 状态 | 含义 |
|------|------|
| `open` | 尚未调查或调查未完成 |
| `closed` | 已验证通过（evidence 必须填具体工具输出） |
| `blocked` | 前置 gate 未关闭，当前 gate 无法开始 |
| `n/a` | 确实不适用（evidence 必须解释为何不适用） |

**二、`_REQUIRED_GATES` 的结构**

它是一个 崩溃签名类型 → 必需 gate 列表 的映射：

```python
_REQUIRED_GATES: ClassVar[Dict[str, List[str]]] = {
    "pointer_corruption": [
        "register_provenance",         # 寄存器来源验证
        "object_lifetime",             # 对象生命周期验证
        "local_corruption_exclusion",  # 排除本地损坏
        "external_corruption_gate",    # 外部损坏门控
        "field_type_classification",   # 字段类型分类
    ],
    "null_deref":     ["register_provenance"],
    "use_after_free": ["register_provenance", "object_lifetime"],
    "soft_lockup":    ["stack_integrity", "lock_holder"],
    # ... 等等
}
```

每种崩溃类型需要的验证深度不同：
- **简单类型**（如 null_deref、divide_error）只需 1-2 个 gate
- **复杂类型**（如 pointer_corruption）需要 5 个 gate，形成完整的证据链

**三、在项目中的作用**

Gates 在整个分析流程中扮演 质量守门人（Quality Gatekeeper）的角色：

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ LLM 逐步推理 │ ──► │ 更新 gates 状态   │ ──► │ 所有 gate closed │
│ 调用 crash   │     │ (open→closed/n/a)│     │ 才能 is_        │
│ 工具收集证据  │     │                  │     │ conclusive=true  │
└─────────────┘     └──────────────────┘     └─────────────────┘
```

具体来说：

- **防止过早结论** — LLM 容易"急于下结论"，gates 强制要求它完成所有必要的验证步骤后才能输出 is_conclusive=True。

- **保证证据完整性** — 例如 pointer_corruption 必须依次验证：寄存器来源 → 对象生命周期 → 排除本地损坏 → 外部损坏判定 → 字段类型分类，形成一条完整的证据链，缺一不可。

- **支持依赖关系** — gate 之间可以有 prerequisite（前置依赖），例如 external_corruption_gate 的前置是 local_corruption_exclusion，必须先排除本地损坏才能判断是否外部损坏。

- **可审计性** — 每个 gate 关闭时必须附带 evidence（具体工具输出），使得整个分析过程可追溯、可验证，不是 LLM 自由发挥的"黑盒推理"。

**四、类比理解**

可以把 gates 想象成飞行检查清单：飞行员在起飞前必须逐项检查并确认（襟翼、燃油、引擎...），全部打勾后才能起飞。同样，分析 Agent 在宣布"找到根因"之前，必须逐个完成对应崩溃类型的检查项，全部 closed 后才能真正输出最终诊断。

**核心概念**：
- **[`CrashSignatureClass`](vmcore-analysis-agent/src/react/schema.py#L117-L134) 与 [`RootCauseClass`](vmcore-analysis-agent/src/react/schema.py#L139-L162)**：前者是从 panic 日志中观察到的症状（例如 `soft_lockup`），后者是推断出的底层机制（例如 `deadlock`）。它们在分析流程中扮演不同的角色。
- **托管状态 ([`active_hypotheses`](vmcore-analysis-agent/src/react/schema.py#L326-L333), [`gates`](vmcore-analysis-agent/src/react/schema.py#L335-L343))**：这些字段并非由 LLM 生成，而是由代理的执行器逻辑维护。它们是代理能够进行透明、可追溯且严谨分析的能力基石。

### 路由逻辑（should_continue / after_crash_tool）

```
def should_continue(state: AgentState) -> str:
    # 1. 发生错误 → 结束
    if error_state and error_state.get("is_error"):
        return "__end__"

    # 2. 需要结构化 reasoning_content → structure_reasoning_node
    if state.get("reasoning_to_structure"):
        return structure_reasoning_node

    # 3. AIMessage 带 tool_calls → crash_tool_node（除非是最后一步）
    if isinstance(last_message, AIMessage) and tool_calls:
        return crash_tool_node if not is_last_step else "__end__"

    # 4. AIMessage 无 tool_calls（is_conclusive=true）→ 结束
    if isinstance(last_message, AIMessage):
        return "__end__"

    # 5. HumanMessage（初始数据收集完毕）→ llm_analysis_node
    if isinstance(last_message, HumanMessage):
        return llm_analysis_node
```

### MCP Crash 工具调用示例

```
# 并发执行多条 crash 命令
async def dispatch_crash_commands(commands, state):
    async with crash_client(state) as tools:
        tasks = [_invoke_tool(tool, cmd, state) for cmd in commands]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return list(zip(commands, results))

# 单条工具调用
result = await tool.ainvoke({
    "command": "bt -c 3",
    "vmcore_path": state["vmcore_path"],
    "vmlinux_path": state["vmlinux_path"],
})
```

## 项目结构

```
vmcore-analysis-agent/
├── README.md                          # 项目说明文档
├── README.zh-CN.md                    # 中文项目说明文档
├── main.py                            # FastAPI 服务入口
├── pyproject.toml                     # Python 项目配置
├── uv.lock                            # 依赖锁定文件
├── .python-version                    # Python 版本规范
├── config/
│   └── config.yml                     # LLM / MCP 配置文件
├── client/
│   ├── client.py                      # HTTP 客户端实现
│   └── main.py                        # 命令行客户端入口
├── src/
│   ├── llm/
│   │   └── model.py                   # DeepSeek LLM 初始化和模型配置
│   ├── react/
│   │   ├── __init__.py                # 包初始化文件
│   │   ├── action_guard.py            # 行动保护和安全验证
│   │   ├── edges.py                   # 路由逻辑和状态转换
│   │   ├── fragment_flags.py          # 片段标志管理
│   │   ├── graph.py                   # LangGraph 图构建
│   │   ├── graph_state.py             # AgentState 定义
│   │   ├── layer0_system.py           # 系统层 Prompt 定义
│   │   ├── llm_node.py                # LLM 调用和响应处理
│   │   ├── llm_runtime.py             # LLM 运行时配置
│   │   ├── logging_callback.py        # 图执行日志回调
│   │   ├── nodes.py                   # 核心节点实现
│   │   ├── output_parser.py           # LLM 输出解析和验证
│   │   ├── playbooks.py               # 分析剧本定义
│   │   ├── prompt_builder.py          # Prompt 构建器
│   │   ├── prompt_layers.py           # Prompt 分层管理
│   │   ├── prompt_overlays.py         # Prompt 覆盖层定义
│   │   ├── prompt_phrases.py          # Prompt 短语模板
│   │   ├── prompts.py                 # 专业分析 Prompt
│   │   ├── report_generator.py        # Markdown 报告生成
│   │   ├── schema.py                  # 数据结构定义 (VMCoreAnalysisStep)
│   │   ├── sop_fragments.py           # 标准操作程序片段
│   │   └── state_manager.py           # 状态管理器
│   ├── mcp_tools/
│   │   ├── crash/                     # crash MCP Server 实现
│   │   │   ├── server.py              # crash MCP 服务器
│   │   │   ├── client.py              # crash MCP 客户端
│   │   │   ├── executor.py            # crash 命令执行器
│   │   │   ├── scsishow.py            # SCSI 显示工具
│   │   │   └── __init__.py            # crash 工具包初始化
│   │   ├── source_patch/              # source_patch MCP Server 实现
│   │   │   ├── server.py              # 源码补丁 MCP 服务器
│   │   │   ├── client.py              # 源码补丁 MCP 客户端
│   │   │   └── __init__.py            # 源码补丁工具包初始化
│   │   ├── stack_canary/              # 栈金丝雀分析工具
│   │   │   ├── server.py              # 栈金丝雀 MCP 服务器
│   │   │   ├── client.py              # 栈金丝雀 MCP 客户端
│   │   │   ├── analyzer.py            # 栈金丝雀分析器
│   │   │   └── __init__.py            # 栈金丝雀工具包初始化
│   │   ├── __init__.py                # MCP 工具包初始化
│   │   └── registry.py                # MCP 工具注册表
│   └── utils/                         # 工具函数
│       ├── config.py                  # 配置管理
│       ├── logging.py                 # 日志配置
│       ├── os.py                      # 操作系统工具函数
│       └── __init__.py                # 工具包初始化
├── simulate-crash/                    # 内核崩溃模拟模块
│   ├── rcu_stall/                     # RCU stall 复现场景
│   ├── soft_lockup/                   # Soft lockup 复现场景
│   └── dma_memory_corruption/         # DMA 内存破坏复现场景
├── reports/                           # 分析报告输出目录
├── tests/                             # 测试套件
│   ├── test_action_guard.py           # 行动保护测试
│   ├── test_crash_client.py           # Crash 客户端测试
│   ├── test_llm_runtime.py            # LLM 运行时测试
│   ├── test_output_parser.py          # 输出解析器测试
│   ├── test_prompt_builder.py         # Prompt 构建器测试
│   ├── test_prompts.py                # Prompt 测试
│   ├── test_schema.py                 # 数据结构测试
│   ├── test_stack_canary_analyzer.py  # 栈金丝雀分析器测试
│   ├── test_stack_canary_client.py    # 栈金丝雀客户端测试
│   ├── test_state_manager.py          # 状态管理器测试
│   └── test_vmcore_analysis_step.py   # VMCore 分析步骤测试
├── tools/
│   ├── show_first_global_func.sh      # 调试符号验证脚本
│   └── test_key.py                    # API 密钥测试脚本
└── test_simple.py                     # 简单集成测试
```

## 快速开始

### 1. 安装依赖

```
cd vmcore-analysis-agent
uv sync
```

### 2. 配置

编辑 `config/config.yml`，配置 LLM API Key 和 MCP 服务路径：

```
llm:
  api_key: "your-deepseek-api-key"
  model: "deepseek-reasoner"
  base_url: "https://api.deepseek.com"
```

### 3. 启动 FastAPI 服务

```
# 方式1：直接运行
python main.py

# 方式2：使用 uvicorn（推荐，支持热加载）
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

服务启动后：
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

### 4. 使用客户端调用分析接口

#### 4.1 检查服务健康状态

```
cd client
uv run main.py --health
```

#### 4.2 标准流式分析（推荐）

```
cd client
uv run main.py --url http://192.168.14.132:8000 --stream \
                       --vmcore-path "/crash_case/Case_04387188/vmcore" \
                       --vmlinux-path "/usr/lib/debug/lib/modules/4.18.0-305.40.2.el8_4.x86_64/vmlinux" \
                       --vmcore-dmesg-path "/crash_case/Case_04387188/vmcore-dmesg.txt"
```

#### 4.3 同步模式分析（可选）

```
uv run client/main.py
```

#### 4.4 自定义参数分析

```
uv run client/main.py --vmcore-path "/path/to/vmcore" \
                      --vmlinux-path "/path/to/vmlinux" \
                      --vmcore-dmesg-path "/path/to/vmcore-dmesg.txt" \
                      --debug-symbols "/path/to/module1.ko" "/path/to/module2.ko"
```

#### 4.5 指定服务地址

```
uv run client/main.py --url http://192.168.1.100:8000 --stream
```

#### 4.6 报告保存选项

```
# 流式模式分析并保存报告到当前目录（默认行为）
uv run client/main.py --stream

# 指定报告输出目录
uv run client/main.py --stream --output-dir ./reports

# 不保存文件，仅显示
uv run client/main.py --stream --no-save
```

#### 4.7 客户端完整参数说明

```
参数说明：
  --url URL                   API 服务地址 (默认: http://localhost:8000)
  --stream                    使用流式模式
  --health                    仅检查服务健康状态
  --vmcore-path PATH          vmcore 文件路径
  --vmlinux-path PATH         vmlinux 调试符号路径
  --vmcore-dmesg-path PATH    vmcore-dmesg.txt 文件路径
  --debug-symbols [PATH ...]  额外的调试符号路径列表
  --timeout SECONDS           请求超时时间（秒）(默认: 600)
  --output-dir DIR            报告输出目录 (默认: 当前目录)
  --no-save                   不保存 markdown 报告文件
```

**说明**：
- 分析完成后，默认会自动保存 markdown 报告到指定目录
- 报告文件名格式：`127.0.0.1-2026-01-30-22-51-43.md`（从服务器 IP 和时间戳生成）
- 报告包含完整的分析过程、推理步骤和最终诊断结论

## 应用场景

1. **自动化故障诊断**：自动分析 vmcore 文件，减少人工干预
2. **知识库构建**：从历史案例中提取可复用的诊断逻辑
3. **新手培训**：作为学习内核调试的教学工具
4. **生产环境监控**：集成到运维平台，实现自动告警分析

## 未来扩展

1. **更多诊断场景**：支持除 lockup 外的其他内核问题
2. **多模态分析**：结合日志、指标等多维度数据
3. **实时分析**：支持在线系统的实时诊断
4. **分布式部署**：支持大规模并发分析

## 附录：第三方驱动调试符号编译指南 (以 mlx5_core 为例)

在分析涉及第三方驱动（如 Mellanox 网卡驱动）的 vmcore 时，如果默认驱动缺少调试符号，需要手动安装源码并重新编译。以下是标准操作流程：

### 1. 环境准备

安装驱动源码包和对应的内核开发包：

```
# 安装 Mellanox 驱动源码
rpm -ivh mlnx-ofa_kernel-source-5.8-OFED.5.8.3.0.7.1.rhel8u4.x86_64.rpm

# 安装对应版本的内核开发包（需与 vmcore 的内核版本一致）
rpm -ivh --oldpackage kernel-devel-4.18.0-305.130.1.el8.x86_64.rpm
```

### 2. 编译带调试信息的模块

进入源码目录并配置编译选项：

```
cd /usr/src/ofa_kernel-5.8/source

# 配置编译选项：开启 debug info，指定内核源码路径
./configure --with-debug-info --with-mlx5-mod \
                 --kernel-sources /usr/src/kernels/4.18.0-305.130.1.el8_4.x86_64 \
                 --cache-file=my_config.cache

# 执行编译
make -j$(nproc)
```

### 3. 验证调试符号

编译完成后，通过以下方式验证模块是否包含调试信息：

```
# 1. 检查 debug section 是否存在
readelf -S drivers/net/ethernet/mellanox/mlx5/core/mlx5_core.ko | grep debug

# 2. 尝试反汇编导出源码（验证源码关联）
objdump -S -d drivers/net/ethernet/mellanox/mlx5/core/mlx5_core.ko > mlx5_debug.asm

# 3. 使用工具脚本验证符号解析
./tools/show_first_global_func.sh /usr/src/ofa_kernel-5.8/source/drivers/net/ethernet/mellanox/mlx5/core/mlx5_core.ko
```

## 贡献指南

欢迎提交 Issue 和 Pull Request 来改进项目。

## 许可证

MIT License