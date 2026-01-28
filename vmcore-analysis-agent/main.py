# main.py
import asyncio
import yaml
import json
from src.utils.logging import logger
from src.utils.config import config_manager
from src.llm.model import create_llm
from src.react.graph import create_agent_graph
from src.react.logging_callback import graph_logging_callback
from src.mcp_tools.crash.client import initialize_crash_tools
from src.mcp_tools.source_patch.client import initialize_patch_tools


async def main():
    logger.info("Hello from vmcore-analysis-agent!")
    logger.info(
        f"config: \n{yaml.dump(config_manager.get_all(), allow_unicode=True, sort_keys=False)}"
    )

    llm = create_llm()
    logger.info(f"LLM Model: {llm.model_name}")

    # 在 async 上下文中初始化 crash 工具
    logger.info("Initializing crash and patch tools...")

    crash_tools = await initialize_crash_tools()
    source_patch_tools = await initialize_patch_tools()

    # 检查是否有可用的工具
    if not crash_tools:
        logger.error("No crash tools available. Please check MCP server configuration.")
        return

    logger.info(f"Loaded {len(crash_tools)} crash tools successfully.")

    all_tools = crash_tools + source_patch_tools
    # 创建 agent 图
    agent_graph = create_agent_graph(llm, all_tools)

    # 使用回调配置
    thread = {"configurable": {"thread_id": "2"}}
    config = {
        "recursion_limit": 20,
        "callbacks": [graph_logging_callback],
        **thread,
    }

    try:
        async for event in agent_graph.astream(
            {
                "vmcore_path": "/var/crash/127.0.0.1-2026-01-28-14:23:29/vmcore",
                "vmlinux_path": "/usr/lib/debug/lib/modules/5.14.0-611.9.1.el9_7.x86_64/vmlinux",
                "vmcore_dmesg_path": "/var/crash/127.0.0.1-2026-01-28-14:23:29/vmcore-dmesg.txt",
                "debug_symbol_paths": [
                    "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/soft_lockup/soft_lockup_module.ko",
                    "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko",
                ],
            },
            config=config,
        ):
            for k, v in event.items():
                if k != "__end__":
                    logger.info(f"📍 Node: {k} execute complete.")
                    # 打印 token 使用情况
                    token_usage = agent_graph.get_state(thread).values.get(
                        "token_usage", 0
                    )
                    logger.info(f"   Token usage so far: {token_usage}")
    except Exception as e:
        logger.error(f"Agent execution failed: {e}", exc_info=True)

    snapshot = agent_graph.get_state(thread)
    # 使用 pretty 打印最终状态

    logger.info(f"Final Agent State: \n{json.dumps(snapshot, indent=2, default=str)}")


if __name__ == "__main__":
    asyncio.run(main())
