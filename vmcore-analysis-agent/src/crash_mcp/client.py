import sys
from pathlib import Path
from langchain_mcp_adapters.client import MultiServerMCPClient

crash_client = MultiServerMCPClient(
    {
        "crash": {
            "command": sys.executable,
            "args": ["-m", "src.crash_mcp.server"],
            "transport": "stdio",
        }
    }
)
