# main.py
import uuid
import yaml
import json
import asyncio
from pathlib import Path
from typing import cast, Optional, Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from src.utils.logging import logger
from src.utils.config import config_manager
from src.llm.model import create_reasoning_llm, create_structured_llm
from src.react.graph import create_agent_graph
from src.react.graph_state import AgentState
from src.react.logging_callback import graph_logging_callback
from src.react.report_generator import generate_markdown_report
from src.mcp_tools.crash.client import initialize_crash_tools
from src.mcp_tools.source_patch.client import initialize_patch_tools

# Agent 图最大递归轮次（LangGraph superstep 上限）
# 注意：每轮可见分析消耗 3 个 superstep：
#   llm_analysis_node (1) + structure_reasoning_node (1) + crash_tool_node (1)
# 加上初始 collect_crash_init_data_node (1)，公式为：1 + N_rounds × 3
# 例如：支持 ~30 轮分析 → 1 + 30×3 = 91；支持 ~40 轮 → 1 + 40×3 = 121
AGENT_RECURSION_LIMIT = 60


# 请求模型
class VmcoreAnalysisRequest(BaseModel):
    vmcore_path: str = Field(..., description="vmcore 文件路径")
    vmlinux_path: str = Field(..., description="vmlinux 调试符号路径")
    vmcore_dmesg_path: str = Field(..., description="vmcore-dmesg.txt 文件路径")
    debug_symbol_paths: list[str] = Field(
        default_factory=list, description="额外的调试符号路径列表"
    )


# 响应模型
class VmcoreAnalysisResponse(BaseModel):
    success: bool
    task_id: str
    agent_answer: str
    token_usage: int
    error: Optional[str] = None


def validate_file_paths(request: VmcoreAnalysisRequest) -> Optional[str]:
    """
    验证请求中的所有文件路径是否存在。

    Args:
        request: VmcoreAnalysisRequest 请求对象

    Returns:
        Optional[str]: 如果验证失败返回错误信息，成功返回 None
    """
    # 检查 vmcore 文件
    if not Path(request.vmcore_path).exists():
        error_msg = f"vmcore file not found: {request.vmcore_path}"
        logger.error(error_msg)
        return error_msg

    # 检查 vmlinux 文件
    if not Path(request.vmlinux_path).exists():
        error_msg = f"vmlinux file not found: {request.vmlinux_path}"
        logger.error(error_msg)
        return error_msg

    # 检查 vmcore-dmesg 文件
    if not Path(request.vmcore_dmesg_path).exists():
        error_msg = f"vmcore-dmesg file not found: {request.vmcore_dmesg_path}"
        logger.error(error_msg)
        return error_msg

    # 检查调试符号文件列表
    for debug_symbol_path in request.debug_symbol_paths:
        if not Path(debug_symbol_path).exists():
            error_msg = f"Debug symbol file not found: {debug_symbol_path}"
            logger.error(error_msg)
            return error_msg

    logger.info("All file paths validated successfully")
    return None


# 全局变量存储初始化的组件
app_state: dict[str, Any] = {
    "reasoning_llm": None,
    "structured_llm": None,
    "crash_tools": None,
    "source_patch_tools": None,
    "agent_graph": None,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Starting vmcore-analysis-agent server...")
    logger.info(
        f"config: \n{yaml.dump(config_manager.get_all(), allow_unicode=True, sort_keys=False)}"
    )

    # 初始化 推理 LLM
    app_state["reasoning_llm"] = create_reasoning_llm()
    logger.info(f"LLM Model: {app_state['reasoning_llm'].model_name}")

    # 初始化结构化 LLM（用于结构化 Reasoner 的纯文本推理内容）
    app_state["structured_llm"] = create_structured_llm()
    logger.info(f"Chat LLM Model: {app_state['structured_llm'].model_name}")

    # 初始化工具
    logger.info("Initializing crash and patch tools...")
    app_state["crash_tools"] = await initialize_crash_tools()
    app_state["source_patch_tools"] = await initialize_patch_tools()

    if not app_state["crash_tools"]:
        logger.error("No crash tools available. Please check MCP server configuration.")
    else:
        logger.info(f"Loaded {len(app_state['crash_tools'])} crash tools successfully.")

    # 创建 agent 图
    all_tools = (app_state["crash_tools"] or []) + (
        app_state["source_patch_tools"] or []
    )
    app_state["agent_graph"] = create_agent_graph(
        app_state["reasoning_llm"],
        all_tools,
        structured_llm=app_state["structured_llm"],
    )
    logger.info("Agent graph created successfully.")

    yield

    # 清理资源
    logger.info("Shutting down vmcore-analysis-agent server...")


app = FastAPI(
    title="Vmcore Analysis Agent API",
    description="基于 LangGraph 的 vmcore 分析智能代理 API",
    version="1.0.0",
    lifespan=lifespan,
)


# Initialize Prometheus instrumentation
Instrumentator().instrument(app).expose(app)


@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {
        "status": "healthy",
        "llm_ready": app_state["llm"] is not None,
        "tools_ready": app_state["crash_tools"] is not None,
    }


@app.post("/analyze", response_model=VmcoreAnalysisResponse)
async def analyze_vmcore(request: VmcoreAnalysisRequest):
    """
    分析 vmcore 文件

    接收 vmcore 相关路径参数，执行分析并返回结果
    """
    if app_state["agent_graph"] is None:
        raise HTTPException(status_code=503, detail="Agent graph not initialized")

    task_id = str(uuid.uuid4())
    logger.info(f"Starting analysis task: {task_id}")
    logger.info(f"Request: vmcore_path={request.vmcore_path}")

    # 验证文件路径
    validation_error = validate_file_paths(request)
    if validation_error:
        return VmcoreAnalysisResponse(
            success=False,
            task_id=task_id,
            agent_answer="",
            token_usage=0,
            error=validation_error,
        )

    # 使用回调配置
    thread = {"configurable": {"thread_id": task_id}}
    config = cast(
        RunnableConfig,
        {
            "recursion_limit": AGENT_RECURSION_LIMIT,
            "callbacks": [graph_logging_callback],
            **thread,
        },
    )

    try:
        initial_state: AgentState = {
            "vmcore_path": request.vmcore_path,
            "vmlinux_path": request.vmlinux_path,
            "vmcore_dmesg_path": request.vmcore_dmesg_path,
            "debug_symbol_paths": request.debug_symbol_paths,
            "messages": [],
            "step_count": 0,
            "token_usage": 0,
            "is_last_step": False,
            "agent_answer": "",
            "error": None,
        }

        async for event in app_state["agent_graph"].astream(
            initial_state,
            config=config,
        ):
            for k, v in event.items():
                if k != "__end__":
                    logger.info(f"📍 Node: {k} execute complete.")
                    token_usage = (
                        app_state["agent_graph"]
                        .get_state(cast(RunnableConfig, thread))
                        .values.get("token_usage", 0)
                    )
                    logger.info(f"   Token usage so far: {token_usage}")

        # 获取最终状态
        snapshot = app_state["agent_graph"].get_state(cast(RunnableConfig, thread))
        final_values = snapshot.values

        # 生成 markdown 报告
        markdown_report = generate_markdown_report(final_values)
        logger.info(f"Task {task_id} completed successfully")
        logger.debug(f"Generated markdown report (length: {len(markdown_report)})")
        logger.info(f"Task {task_id} finished, report generation complete.")

        return VmcoreAnalysisResponse(
            success=True,
            task_id=task_id,
            agent_answer=markdown_report,
            token_usage=final_values.get("token_usage", 0),
            error=final_values.get("error"),
        )

    except asyncio.CancelledError:
        logger.warning(f"Task {task_id} was cancelled by system/user.")
        # 重新抛出以便 server 处理关闭
        raise

    except Exception as e:
        logger.error(f"Agent execution failed: {e}", exc_info=True)
        return VmcoreAnalysisResponse(
            success=False,
            task_id=task_id,
            agent_answer="",
            token_usage=0,
            error=str(e),
        )


@app.post("/analyze/stream")
async def analyze_vmcore_stream(request: VmcoreAnalysisRequest):
    """
    流式分析 vmcore 文件

    以 Server-Sent Events 格式返回分析进度
    """
    if app_state["agent_graph"] is None:
        raise HTTPException(status_code=503, detail="Agent graph not initialized")

    task_id = str(uuid.uuid4())
    logger.info(f"Starting stream analysis task: {task_id}")

    # 验证文件路径
    validation_error = validate_file_paths(request)
    if validation_error:

        async def error_generate():
            yield f"data: {json.dumps({'event': 'error', 'error': validation_error})}\n\n"

        return StreamingResponse(error_generate(), media_type="text/event-stream")

    async def generate():
        thread = {"configurable": {"thread_id": task_id}}
        config = cast(
            RunnableConfig,
            {
                "recursion_limit": AGENT_RECURSION_LIMIT,
                "callbacks": [graph_logging_callback],
                **thread,
            },
        )

        try:
            initial_state: AgentState = {
                "vmcore_path": request.vmcore_path,
                "vmlinux_path": request.vmlinux_path,
                "vmcore_dmesg_path": request.vmcore_dmesg_path,
                "debug_symbol_paths": request.debug_symbol_paths,
                "messages": [],
                "step_count": 0,
                "token_usage": 0,
                "is_last_step": False,
                "agent_answer": "",
                "error": None,
            }

            yield f"data: {json.dumps({'event': 'start', 'task_id': task_id})}\n\n"

            # 使用队列 + 心跳机制：将 astream 放入独立 Task，
            # 每 15s 发送一个 SSE 注释心跳，防止客户端因"无数据"断连。
            # （crash 工具执行 log / search 等大命令时可能超过 2 分钟，
            #   executor 的 COMMAND_TIMEOUT=120s 会终止进程，但在此期间
            #   SSE 连接需保持活跃。）
            event_queue: asyncio.Queue = asyncio.Queue()

            async def _run_graph():
                try:
                    async for ev in app_state["agent_graph"].astream(
                        initial_state,
                        config=config,
                        stream_mode="updates",
                    ):
                        await event_queue.put(("event", ev))
                except Exception as exc:
                    await event_queue.put(("error", exc))
                finally:
                    await event_queue.put(("done", None))

            graph_task = asyncio.create_task(_run_graph())

            HEARTBEAT_INTERVAL = 15  # 秒
            while True:
                try:
                    kind, payload = await asyncio.wait_for(
                        event_queue.get(), timeout=HEARTBEAT_INTERVAL
                    )
                except asyncio.TimeoutError:
                    # 心跳：SSE 注释行，客户端忽略，但可刷新 TCP keep-alive
                    yield ": heartbeat\n\n"
                    continue

                if kind == "done":
                    break
                elif kind == "error":
                    raise payload
                else:
                    # kind == "event"
                    for node_name, node_output in payload.items():
                        if node_name != "__end__":
                            snapshot = app_state["agent_graph"].get_state(
                                cast(RunnableConfig, thread)
                            )
                            token_usage = snapshot.values.get("token_usage", 0)
                            step_count = snapshot.values.get("step_count", 0)
                            yield f"data: {json.dumps({'event': 'node_complete', 'node': node_name, 'token_usage': token_usage, 'step': step_count})}\n\n"

            await graph_task  # 确保异常被传播

            snapshot = app_state["agent_graph"].get_state(cast(RunnableConfig, thread))
            final_values = snapshot.values

            # 生成 markdown 报告
            markdown_report = generate_markdown_report(final_values)
            logger.info(f"Task {task_id} finished, report generation complete.")

            yield f"data: {json.dumps({'event': 'complete', 'agent_answer': markdown_report, 'token_usage': final_values.get('token_usage', 0), 'error': final_values.get('error')})}\n\n"

        except asyncio.CancelledError:
            logger.warning(f"Stream task {task_id} was cancelled.")
            yield f"data: {json.dumps({'event': 'error', 'error': 'Task cancelled'})}\n\n"
            raise

        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    # 配置 uvicorn 以支持长时间运行的请求
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        timeout_keep_alive=3600,  # 保持连接 1 小时（针对长时间分析任务）
        timeout_graceful_shutdown=60,  # 优雅关闭超时 60 秒
    )
