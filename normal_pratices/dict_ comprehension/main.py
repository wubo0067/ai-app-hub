# 定义一个简单的类，用于模拟工具对象
class Tool:
    def __init__(self, name, description):
        self.name = name
        self.description = description

    def __repr__(self):
        return f"Tool(name='{self.name}', description='{self.description}')"


# 创建一些工具对象
tool1 = Tool("add", "Adds two numbers")
tool2 = Tool("multiply", "Multiplies two numbers")
tool3 = Tool("divide", "Divides two numbers")

# 将工具对象放入列表中
tools = [tool1, tool2, tool3]

# 使用字典推导式创建以工具名称为键的字典
tools_by_name = {tool.name: tool for tool in tools}

# 输出结果
print(tools_by_name)

# 使用 tools_by_name，通过 name 循环找到 tool 对象
print(f"find tool 'multiply': {tools_by_name['multiply']}")
