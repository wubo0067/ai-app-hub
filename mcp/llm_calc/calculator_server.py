from fastmcp import FastMCP

# 创建 mcp 服务器
mcp = FastMCP("Calculator Server")

@mcp.tool()
def add(a: float, b: float) -> float:
    """计算两个浮点数的和"""
    return a + b

@mcp.tool()
def mul(a: float, b: float) -> float:
    """计算两个浮点数的积"""
    return a * b

@mcp.tool()
def calculate_expression(expression: str) -> float:
    """计算一个数学表达式的值"""
    try:
        # 注意：使用 eval 存在安全风险，实际应用中应使用更安全的解析器
        return eval(expression)
    except Exception as e:
        return f"Error evaluating expression: {str(e)}"

if __name__ == "__main__":
    mcp.run()