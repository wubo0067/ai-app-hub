import sys
import asyncio
from pathlib import Path
from langchain_mcp_adapters.client import MultiServerMCPClient

crash_client = MultiServerMCPClient(
    {
        "crash": {
            "command": sys.executable,
            "args": ["-m", "src.mcp_tools.crash.server"],
            "transport": "stdio",
        }
    }
)


# Helper function to get tools synchronously
def get_tools_sync():
    async def get_tools_async():
        return await crash_client.get_tools()

    # 同步函数调用异步函数
    return asyncio.run(get_tools_async())


crash_tools = get_tools_sync()
