import asyncio
from fastmcp import Client

# http server
client = Client("http://localhost:8000/mcp")

async def main():
    async with client:
        # Basic server interaction
        await client.ping()

        # List available tools
        tools = await client.list_tools()
        print("Available tools:", tools)

        resources = await client.list_resources()
        print("Available resources:", resources)

        prompts = await client.list_prompts()
        print("Available prompts:", prompts)

        # call tool
        result = await client.call_tool("greet", {"name": "calmwu"})
        print(result)

asyncio.run(main())