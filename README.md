# VMCore Analysis Agent

[🇨🇳 Chinese Documentation](./README.zh-CN.md)

An intelligent Linux kernel crash (vmcore) analysis agent based on LangGraph ReAct pattern and MCP tools.

## Project Introduction

### Linux Kernel Crash Analysis

Linux kernel crash analysis is the crown jewel of system engineering and one of the most challenging technical problems.

**Challenges and Difficulties**:
- **Complex knowledge system**: Requires proficiency in C language, operating system principles, common data structures and algorithms, and kernel subsystem architecture (memory management, scheduling, file systems, etc.).
- **Extremely high skill requirements**: Need to master various hardware working principles and kernel basic architecture implementation, and be proficient in complex debugging tools like crash and gdb.
- **High reasoning ability threshold**: Requires extremely strong logical reasoning and analytical skills to extract insights from massive stacks and memory data.

**Core Project Advantages**:
This project innovatively introduces an **ReAct (Reasoning + Acting)** AI Agent architecture combined with **MCP (Model Context Protocol)** tool system, achieving automated vmcore deep analysis:
- **Expert experience digitization**: Replicates RHEL senior engineers' analytical thinking through carefully designed professional prompts, enabling LLMs to learn top experts' reasoning paths.
- **Intelligent tool usage**: AI can autonomously plan and execute crash debugging commands, dynamically exploring memory scenes like human experts rather than simple static matching.
- **Transparent analysis process**: Provides complete chain of thought (Chain of Thought) and operation records, showing not only conclusions but also the complete analytical logic.

## Architecture Design

### Overall Architecture Diagram

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
        F -->|Initial data ready | G["llm_analysis_node\nDeepSeek-Reasoner"]
        G -->|AIMessage with tool_calls| H{should_continue}
        H -->|Need tools| I[crash_tool_node]
        H -->|reasoning_to_structure| J["structure_reasoning_node\ndeepseek-chat"]
        H -->|Analysis complete| K[__end__]
        I -->|after_crash_tool| G
        J -->|Structured| H
    end

    subgraph "MCP Tools"
        I -->|crash commands| L["crash MCP Server\nmcp_tools/crash/server.py"]
        I -->|Source code patches| M["source_patch MCP Server\nmcp_tools/source_patch"]
        L --> N["crash utility\nvmcore + vmlinux"]
        M --> O[unified diff patch]
    end

    K --> D
    D -->|Markdown report| P[reports]
```

**Architecture Diagram Explanation**:
- **Solid arrows** represent data flow or invocation relationships
- **Curly brace nodes** (e.g., `{should_continue}`) represent conditional routing decisions
- **Bracket nodes** represent concrete functional nodes or external services
- The flow starts from `START`, goes through initial data collection, enters the loop of LLM analysis and tool invocation, and ends when `is_conclusive=true` or the recursion limit is reached

## Vmcore Analysis React Agent

### Agent Architecture

ReAct (Reasoning-Action) agent based on LangGraph, containing four core nodes:

| Node | Description |
|------|------|
| `collect_crash_init_data_node` | Initial node, concurrently executes `sys`, `bt` and other commands to collect vmcore basic information |
| `llm_analysis_node` | Calls DeepSeek-Reasoner, outputs structured `VMCoreAnalysisStep`, decides next action |
| `crash_tool_node` | Parses LLM tool call requests, concurrently executes crash commands via MCP |
| `structure_reasoning_node` | When Reasoner returns plain text `reasoning_content`, uses deepseek-chat to structure it (optional) |

### Node Flow Diagram

```
stateDiagram-v2
    [*] --> collect_crash_init_data_node
    collect_crash_init_data_node --> llm_analysis_node : HumanMessage (basic info)

    state llm_analysis_node {
        [*] --> DeepSeek_Reasoner
        DeepSeek_Reasoner --> has_tool_calls : Needs more data
        DeepSeek_Reasoner --> reasoning_to_structure : Reasoner plain text output
        DeepSeek_Reasoner --> analysis_complete : is_conclusive = true
    }

    llm_analysis_node --> crash_tool_node : has tool_calls
    llm_analysis_node --> structure_reasoning_node : reasoning_to_structure
    llm_analysis_node --> [*] : analysis_complete

    crash_tool_node --> llm_analysis_node : ToolMessage (command results)
    crash_tool_node --> [*] : is_last_step

    structure_reasoning_node --> llm_analysis_node : Structured AIMessage
```

### Detailed Node Description

#### Node 1: collect_crash_init_data_node

**Function**: Executes default crash command set, collects vmcore basic information

**Default Commands**:
```python
DEFAULT_CRASH_COMMANDS = [
    "sys",  # System information (kernel version, crash time, etc.)
    "bt",   # Stack backtrace at crash scene
]
```

Execution results are written to the message queue as `HumanMessage`, serving as initial context for LLM analysis.

#### Node 2: llm_analysis_node

**Function**: Calls DeepSeek-Reasoner to output structured analysis steps following the ReAct pattern

**Core Implementation Logic** ([`call_llm_analysis`](vmcore-analysis-agent/src/react/llm_node.py#L27-L212) function):

1. **Message Compression**: Compresses historical messages via [`compress_messages_for_llm`](vmcore-analysis-agent/src/react/llm_runtime.py#L104-L134) to prevent token explosion from reasoning_content accumulation
2. **System Prompt Construction**: Uses [`analysis_crash_prompt`](vmcore-analysis-agent/src/react/prompts.py#L8-L1936), including:
   - Role definition: Senior Linux Kernel Crash Dump analysis expert
   - Output contract: Strictly follows `VMCoreLLMAnalysisStep` JSON Schema
   - Final step constraint: When `is_last_step=True`, must provide conclusion and prohibit tool calls
3. **Structured Output**: Uses `llm_with_tools.with_structured_output(VMCoreLLMAnalysisStep, method="json_mode", include_raw=True)`
4. **Error Handling**:
   - Attempts JSON repair on format errors ([`repair_structured_output`](vmcore-analysis-agent/src/react/output_parser.py#L56-L94))
   - Routes plain text `reasoning_content` to [`structure_reasoning_node`](vmcore-analysis-agent/src/react/llm_node.py#L215-L350)
   - Injects HumanMessage on empty response to force LLM action or conclusion
5. **State Management**: Merges LLM output with managed state (hypotheses, gates) via [`project_managed_analysis_step`](vmcore-analysis-agent/src/react/state_manager.py#L123-L198)

**Core Prompt Design**:
- Built-in anti-repetition policy to prevent executing the same command repeatedly
- Prohibits high-risk commands that trigger token overflow (`sym -l`, `bt -a`, `ps -m`, etc.)
- Supports `run_script` for batch execution, ensuring module symbols are loaded within the same crash session
- Emphasizes evidence-based reasoning, prohibiting speculation without diagnostic evidence

**Output Schema** ([`VMCoreLLMAnalysisStep`](vmcore-analysis-agent/src/react/schema.py#L70-L114)):
```json
{
  "step_id": 1,
  "reasoning": "3-6 sentence structured analytic summary: (1) What did I learn from latest tool output? (2) How does this update hypotheses? (3) What is the ONE most diagnostic next action and why?",
  "action": { "command_name": "bt", "arguments": ["-c", "3"] },
  "is_conclusive": false,
  "signature_class": "soft_lockup",
  "root_cause_class": null,
  "partial_dump": "full",
  "active_hypotheses": null,  // Executor-managed, LLM omits
  "gates": null,              // Executor-managed, LLM omits
  "final_diagnosis": null,
  "fix_suggestion": null,
  "confidence": null,
  "additional_notes": null
}
```

**Token Management**:
- Records token consumption per invocation (`usage_metadata`)
- Accumulates `token_usage` in state for monitoring and optimization

#### Node 3: crash_tool_node

**Function**: Receives LLM tool call requests, concurrently executes crash commands via MCP

**Technical Implementation**:
- Uses `langchain-mcp-adapters` to load MCP tools
- Concurrently executes multiple commands via `asyncio.gather` for efficiency
- Independently manages MCP session lifecycle for each call
- Safely encapsulates tool calls, captures exceptions and returns error strings without interrupting workflow

**Supported MCP Tool Services**:
- **crash MCP Server**: Provides `run_script` and various crash subcommands (`bt`, `dis`, `struct`, `kmem`, `sym`, etc.)
- **source_patch MCP Server**: Generates unified diff patch files based on LLM's source code analysis conclusions

#### Node 4: structure_reasoning_node (Optional)

**Function**: DeepSeek-Reasoner sometimes returns only `reasoning_content` plain text with empty `content`. This node converts it into valid `VMCoreAnalysisStep` JSON using deepseek-chat to ensure proper graph flow.

### Core Data Models

The agent's reasoning and state are structured around a set of Pydantic models defined in `src/react/schema.py`. These models ensure strict, type-safe communication between the LLM and the executor, and enforce a disciplined analytical process.

| Model | Description |
|---|---|
| [`VMCoreAnalysisStep`](vmcore-analysis-agent/src/react/schema.py#L230-L373) | The primary data structure representing a single step in the analysis. It contains the LLM's reasoning, the requested action ([`ToolCall`](vmcore-analysis-agent/src/react/schema.py#L11-L34)), and crucially, the managed state: [`active_hypotheses`](vmcore-analysis-agent/src/react/schema.py#L326-L333) and [`gates`](vmcore-analysis-agent/src/react/schema.py#L335-L343). |
| [`VMCoreLLMAnalysisStep`](vmcore-analysis-agent/src/react/schema.py#L70-L114) | The minimal subset of [`VMCoreAnalysisStep`](vmcore-analysis-agent/src/react/schema.py#L230-L373) that the LLM is expected to output directly. The executor enriches this with the managed state fields. |
| [`FinalDiagnosis`](vmcore-analysis-agent/src/react/schema.py#L45-L67) | A comprehensive record of the final conclusion, populated only when [`is_conclusive`](vmcore-analysis-agent/src/react/schema.py#L294-L294) is `true`. |
| [`Hypothesis`](vmcore-analysis-agent/src/react/schema.py#L165-L192) | Represents a single candidate root cause being tracked by the agent. The [`active_hypotheses`](vmcore-analysis-agent/src/react/schema.py#L326-L333) list forces explicit management of competing theories. |
| [`GateEntry`](vmcore-analysis-agent/src/react/schema.py#L195-L220) | Represents a mandatory verification checkpoint. The [`gates`](vmcore-analysis-agent/src/react/schema.py#L335-L343) dictionary ensures all required evidence is gathered before a conclusive diagnosis is allowed. |

**Key Concepts**:
- **[`CrashSignatureClass`](vmcore-analysis-agent/src/react/schema.py#L117-L134) vs [`RootCauseClass`](vmcore-analysis-agent/src/react/schema.py#L139-L162)**: The former is an observable symptom from the panic log (e.g., `soft_lockup`), while the latter is the inferred underlying mechanism (e.g., `deadlock`). They serve different purposes in the analysis flow.
- **Managed State ([`active_hypotheses`](vmcore-analysis-agent/src/react/schema.py#L326-L333), [`gates`](vmcore-analysis-agent/src/react/schema.py#L335-L343))**: These fields are not generated by the LLM but are maintained by the agent's executor logic. They are the backbone of the agent's ability to perform transparent, traceable, and rigorous analysis.

### Routing Logic (should_continue / after_crash_tool)

```
def should_continue(state: AgentState) -> str:
    # 1. Error occurred → End
    if error_state and error_state.get("is_error"):
        return "__end__"

    # 2. Need to structure reasoning_content → structure_reasoning_node
    if state.get("reasoning_to_structure"):
        return structure_reasoning_node

    # 3. AIMessage with tool_calls → crash_tool_node (unless it's the last step)
    if isinstance(last_message, AIMessage) and tool_calls:
        return crash_tool_node if not is_last_step else "__end__"

    # 4. AIMessage without tool_calls (is_conclusive=true) → End
    if isinstance(last_message, AIMessage):
        return "__end__"

    # 5. HumanMessage (initial data collection complete) → llm_analysis_node
    if isinstance(last_message, HumanMessage):
        return llm_analysis_node
```

### MCP Crash Tool Call Example

```
# Concurrently execute multiple crash commands
async def dispatch_crash_commands(commands, state):
    async with crash_client(state) as tools:
        tasks = [_invoke_tool(tool, cmd, state) for cmd in commands]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return list(zip(commands, results))

# Single tool call
result = await tool.ainvoke({
    "command": "bt -c 3",
    "vmcore_path": state["vmcore_path"],
    "vmlinux_path": state["vmlinux_path"],
})
```

## Project Structure

```
vmcore-analysis-agent/
├── README.md                          # Project documentation
├── README.zh-CN.md                    # Chinese project documentation
├── main.py                            # FastAPI service entry point
├── pyproject.toml                     # Python project configuration
├── uv.lock                            # Dependency lock file
├── .python-version                    # Python version specification
├── config/
│   └── config.yml                     # LLM / MCP configuration file
├── client/
│   ├── client.py                      # HTTP client implementation
│   └── main.py                        # Command line client entry point
├── src/
│   ├── llm/
│   │   └── ...                        # DeepSeek LLM initialization and utilities
│   ├── react/
│   │   ├── __init__.py                # Package initialization
│   │   ├── graph.py                   # LangGraph graph construction
│   │   ├── nodes.py                   # Core node implementations
│   │   ├── edges.py                   # Routing logic and state transitions
│   │   ├── graph_state.py             # AgentState definition
│   │   ├── llm_node.py                # LLM calling and response handling
│   │   ├── llm_runtime.py             # LLM runtime configuration
│   │   ├── output_parser.py           # LLM output parsing and validation
│   │   ├── prompts.py                 # Professional analysis prompts
│   │   ├── report_generator.py        # Markdown report generation
│   │   ├── schema.py                  # Data schema definitions (VMCoreAnalysisStep)
│   │   └── logging_callback.py        # Graph execution log callback
│   ├── mcp_tools/
│   │   ├── crash/                     # crash MCP Server implementation
│   │   │   └── ...                    # crash command executor and client
│   │   └── source_patch/              # source_patch MCP Server implementation
│   │       └── ...                    # patch generation tools
│   └── utils/                         # Utility functions (logging, config, etc.)
├── simulate-crash/                    # Kernel crash simulation module
│   ├── rcu_stall/                     # RCU stall reproduction scenarios
│   ├── soft_lockup/                   # Soft lockup reproduction scenarios
│   └── ...                            # Additional crash scenarios
├── reports/                           # Analysis report output directory
├── logs/                              # Runtime log directory
├── tests/                             # Test suite
│   └── ...                            # Unit and integration tests
└── tools/
    └── show_first_global_func.sh      # Debug symbol verification script
```

## Quick Start

### 1. Install Dependencies

```bash
cd vmcore-analysis-agent
uv sync
```

### 2. Configuration

Edit `config/config.yml` to configure LLM API Key and MCP service paths:

```yaml
llm:
  api_key: "your-deepseek-api-key"
  model: "deepseek-reasoner"
  base_url: "https://api.deepseek.com"
```

### 3. Start FastAPI Service

```bash
# Method 1: Direct run
python main.py

# Method 2: Using uvicorn (recommended, supports hot reload)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

After service startup:
- API documentation: http://localhost:8000/docs
- Health check: http://localhost:8000/health

### 4. Use Client to Call Analysis API

#### 4.1 Check Service Health Status

```bash
uv run client/main.py --health
```

#### 4.2 Standard Streaming Analysis (Recommended)

```bash
uv run client/main.py --url http://192.168.14.132:8000 --stream \
                       --vmcore-path "/crash_case/Case_04387188/vmcore" \
                       --vmlinux-path "/usr/lib/debug/lib/modules/4.18.0-305.40.2.el8_4.x86_64/vmlinux" \
                       --vmcore-dmesg-path "/crash_case/Case_04387188/vmcore-dmesg.txt"
```

#### 4.3 Synchronous Mode Analysis (Optional)

```bash
uv run client/main.py
```

#### 4.4 Custom Parameter Analysis

```bash
uv run client/main.py --vmcore-path "/path/to/vmcore" \
                      --vmlinux-path "/path/to/vmlinux" \
                      --vmcore-dmesg-path "/path/to/vmcore-dmesg.txt" \
                      --debug-symbols "/path/to/module1.ko" "/path/to/module2.ko"
```

#### 4.5 Specify Service Address

```bash
uv run client/main.py --url http://192.168.1.100:8000 --stream
```

#### 4.6 Report Saving Options

```
# Streaming analysis and save report to current directory (default behavior)
uv run client/main.py --stream

# Specify report output directory
uv run client/main.py --stream --output-dir ./reports

# Don't save file, display only
uv run client/main.py --stream --no-save
```

#### 4.7 Complete Client Parameter Description

```
Parameter description:
  --url URL                   API service address (default: http://localhost:8000)
  --stream                    Use streaming mode
  --health                    Check service health status only
  --vmcore-path PATH          vmcore file path
  --vmlinux-path PATH         vmlinux debug symbol path
  --vmcore-dmesg-path PATH    vmcore-dmesg.txt file path
  --debug-symbols [PATH ...]  Additional debug symbol path list
  --timeout SECONDS           Request timeout in seconds (default: 600)
  --output-dir DIR            Report output directory (default: current directory)
  --no-save                   Don't save markdown report file
```

**Note**:
- After analysis completion, markdown reports are automatically saved to the specified directory by default
- Report filename format: `127.0.0.1-2026-01-30-22-51-43.md` (generated from server IP and timestamp)
- Reports include complete analysis process, reasoning steps, and final diagnostic conclusions

## Application Scenarios

1. **Automated fault diagnosis**: Automatically analyze vmcore files, reducing manual intervention
2. **Knowledge base construction**: Extract reusable diagnostic logic from historical cases
3. **Novice training**: Serve as a teaching tool for learning kernel debugging
4. **Production environment monitoring**: Integrate into operations platforms for automatic alert analysis

## Future Extensions

1. **More diagnostic scenarios**: Support other kernel issues beyond lockups
2. **Multimodal analysis**: Combine logs, metrics, and other multidimensional data
3. **Real-time analysis**: Support real-time diagnostics for online systems
4. **Distributed deployment**: Support large-scale concurrent analysis

## Appendix: Third-party Driver Debug Symbol Compilation Guide (mlx5_core example)

When analyzing vmcores involving third-party drivers (e.g., Mellanox NIC drivers), if the default driver lacks debug symbols, you need to manually install the source code and recompile. Below is the standard operating procedure:

### 1. Environment Preparation

Install driver source package and corresponding kernel development package:

```bash
# Install Mellanox driver source
rpm -ivh mlnx-ofa_kernel-source-5.8-OFED.5.8.3.0.7.1.rhel8u4.x86_64.rpm

# Install corresponding kernel development package (must match vmcore kernel version)
rpm -ivh --oldpackage kernel-devel-4.18.0-305.130.1.el8.x86_64.rpm
```

### 2. Compile Module with Debug Information

Enter source directory and configure compilation options:

```bash
cd /usr/src/ofa_kernel-5.8/source

# Configure compilation options: enable debug info, specify kernel source path
./configure --with-debug-info --with-mlx5-mod \
                 --kernel-sources /usr/src/kernels/4.18.0-305.130.1.el8_4.x86_64 \
                 --cache-file=my_config.cache

# Execute compilation
make -j$(nproc)
```

### 3. Verify Debug Symbols

After compilation, verify if the module contains debug information:

```bash
# 1. Check if debug section exists
readelf -S drivers/net/ethernet/mellanox/mlx5/core/mlx5_core.ko | grep debug

# 2. Try disassembling to export source code (verify source association)
objdump -S -d drivers/net/ethernet/mellanox/mlx5/core/mlx5_core.ko > mlx5_debug.asm

# 3. Use tool script to verify symbol resolution
./tools/show_first_global_func.sh /usr/src/ofa_kernel-5.8/source/drivers/net/ethernet/mellanox/mlx5/core/mlx5_core.ko
```

## Contribution Guidelines

Issues and Pull Requests are welcome to improve the project.

## License

MIT License