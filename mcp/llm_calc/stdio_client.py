import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI
import json
import os

class MCPCalculatorClient:
    def __init__(self, server_script_path: str):
        self.server_script_path = server_script_path
        if not os.path.exists(server_script_path):
            raise FileNotFoundError(f"Server script not found: {server_script_path}")
        self.tools = []
        self.session = None
        self._stdio_context = None
        self._session_context = None

    async def __aenter__(self):
        """作为异步上下文管理器进入"""
        server_params = StdioServerParameters(
            command='python',
            args=[self.server_script_path]
        )

        # 启动服务器并保持上下文
        self._stdio_context = stdio_client(server_params)
        read_stream, write_stream = await self._stdio_context.__aenter__()

        # 创建会话并保持上下文
        self._session_context = ClientSession(read_stream, write_stream)
        self.session = await self._session_context.__aenter__()

        # 初始化连接
        await self.session.initialize()

        # 获取工具列表
        tools_result = await self.session.list_tools()
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or f"Tool: {tool.name}",
                    "parameters": {
                        "type": "object",
                        "properties": tool.inputSchema.get("properties", {}),
                        "required": tool.inputSchema.get("required", [])
                    }
                }
            }
            for tool in tools_result.tools
        ]

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """作为异步上下文管理器退出"""
        if self._session_context:
            await self._session_context.__aexit__(exc_type, exc_val, exc_tb)
        if self._stdio_context:
            await self._stdio_context.__aexit__(exc_type, exc_val, exc_tb)

    async def call_tool(self, tool_name: str, arguments: dict):
        """调用 MCP 服务器中的工具"""
        if not self.session:
            raise RuntimeError("Client not connected. Use 'async with' to connect.")
        result = await self.session.call_tool(tool_name, arguments)
        return result.content[0].text if result.content else ""

async def main():
    # 使用 async with 确保正确的资源管理
    async with MCPCalculatorClient("mcp/llm_calc/calculator_server.py") as mcp_client:
        # 配置 OpenAI 客户端
        client = OpenAI(
            api_key='sk-b5480f840a794c69a0af1732459f3ae4',
            base_url="https://api.deepseek.com"
        )

        # 发送聊天请求
        messages = [
            {"role": "user", "content": "计算 12.5 加上 7.3, 然后再乘以 0.5，并返回结果。"}
        ]

        while True:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                tools=mcp_client.tools,
                tool_choice="auto"
            )

            message = response.choices[0].message
            messages.append(message)

            if message.tool_calls:
                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    arguments = json.loads(tool_call.function.arguments)

                    print(f"AI Calling tool: {tool_name} with arguments: {arguments}")

                    tool_result = await mcp_client.call_tool(tool_name, arguments)
                    print(f"Tool result: {tool_result}")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(tool_result)
                    })
            else:
                print(f"AI: {message.content}")
                break

if __name__ == "__main__":
    asyncio.run(main())