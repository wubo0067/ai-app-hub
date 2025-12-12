import asyncio
from fastmcp import FastMCP, Client

# 设置 server 服务器
mcp = FastMCP("My MCP Server")

@mcp.tool()
def greet(name: str) -> str:
    return f"Hello, {name}!"

# 设置 client 服务器
client = Client(mcp)

# client.call_tool => 类似 request.post
async def call_tool(name: str):
    async with client:
        result = await client.call_tool("greet", {"name": name})
        print(result)

asyncio.run(call_tool("Ford"))