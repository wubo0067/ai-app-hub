# Project Architecture: VMCore Analysis Agent

## Project Overview
The VMCore Analysis Agent is an intelligent, hypothesis-driven diagnostic agent designed to analyze Linux kernel core dumps (vmcores). It leverages the Model Context Protocol (MCP) to interact with specialized crash analysis tools and utilizes LangGraph to manage complex, multi-step reasoning loops.

## Technology Stack
- **Language**: Python 3.11+
- **Agent Orchestration**: LangGraph (state machine based reasoning)
- **LLM Framework**: LangChain
- **LLM Models**: Reasoning LLMs (e.g., DeepSeek-Reasoner) and Structured LLMs
- **API Framework**: FastAPI
- **Communication Protocol**: Model Context Protocol (MCP) for tool integration
- **Package Management**: uv

## Core Modules

### 1. API & Entry Point (`main.py`)
- Provides RESTful endpoints for triggering analysis (`/analyze`, `/analyze/stream`).
- Manages the application lifecycle and initializes the core components (LLMs, MCP tools, Agent Graph).
- Handles task tracking and streaming of reasoning progress via Server-Sent Events (SSE).

### 2. Reasoning Engine (`src/react/`)
The heart of the agent, implemented using a LangGraph state machine.
- **`graph.py`**: Defines the topology of the agent's decision-making process.
- **`nodes.py`**: Implements the core operational nodes (Initialization, LLM Analysis, Tool Execution).
- **`graph_state.py`**: Defines `AgentState`, a complex schema that tracks messages, evidence, tool outputs, hypotheses, and reasoning progress.
- **`action_guard.py`**: A safety layer that validates tool calls against the current kernel crash context to prevent invalid or dangerous commands.
- **`evidence.py`**: Manages the collection and validation of "facts" extracted from tool outputs to drive hypothesis testing.
- **`prompts.py`**: Manages complex, multi-layered prompt construction for different reasoning stages.

### 3. Tooling Layer (`src/mcp_tools/`)
- Implements specialized tools for kernel crash analysis through the Model Context Protocol (MCP).
- **Tools categories**: Crash diagnostics, source code analysis, stack canary detection, etc.
- **`registry.py`**: Manages tool discovery and provider registration.

### 4. LLM Interface (`src/llm/`)
- Provides abstractions for creating different types of LLM instances:
    - **Reasoning LLM**: Used for high-level planning and decision making.
    - **Structured LLM**: Used for generating structured reasoning content or fallback reasoning.

### 5. Utilities (`src/utils/`)
- Common functionalities: configuration management, logging, and file handling.

## Data Flow & Reasoning Process

The agent follows a continuous loop of **Observe -> Orient -> Decide -> Act**:

1. **Initialization**: The agent starts by collecting baseline diagnostic information (e.g., kernel version, CPU/PID info from `bt`) via the `collect_crash_init_data_node`.
2. **Reasoning (Observe/Orient)**: The `llm_analysis_node` examines the current `AgentState` (including recent tool outputs and gathered evidence) to formulate hypotheses about the root cause.
3. **Decision (Decide)**: Based on its reasoning, the LLM decides to:
    - Call a tool to gather more evidence (e.g., `struct` inspection, `log` analysis).
    - Perform a structured reasoning step to refine its internal state.
    - Conclude the analysis if sufficient evidence is gathered.
4. **Action (Act)**: If a tool is selected, the `crash_tool_node` executes the command. Before execution, the `action_guard` validates the command parameters against the known kernel state to ensure correctness.
5. **Feedback Loop**: The results of the tool execution are added to the `AgentState`, and the loop restarts from the reasoning phase.
6. **Reporting**: Once a conclusion is reached, the `report_generator` transforms the accumulated evidence and reasoning into a comprehensive Markdown report.

## Key Technical Decisions

- **Hypothesis-Driven Reasoning**: Instead of a linear script, the agent uses a state machine that manages "active hypotheses" and "gates." This allows the agent to pivot its investigation when new evidence contradicts its current assumptions.
- **Safety via Action Guard**: To mitigate the risk of LLM "hallucinating" invalid kernel memory addresses or commands, a dedicated guard validates tool calls against the actual observed kernel structures.
- **MCP for Extensibility**: Using MCP allows the core reasoning logic to be decoupled from the specific implementation of crash tools, making it easy to add new diagnostic capabilities.
- **State-Centric Design**: By using a single, rich `AgentState` in LangGraph, the agent maintains a consistent "world view" across multiple reasoning turns, facilitating complex, long-running investigations.
