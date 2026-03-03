"""
调试 with_structured_output 返回 None 的问题
"""

import os
import json
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from typing import List


# 简单的测试模型
class SimpleTest(BaseModel):
    name: str
    items: List[str]


def test_structured_output():
    llm = ChatOpenAI(
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0,
    )

    # 测试1: 简单的结构化输出
    print("=== 测试1: 简单结构化输出 ===")
    simple_chain = llm.with_structured_output(SimpleTest, method="function_calling")
    result1 = simple_chain.invoke("创建一个名为'test'的对象，包含3个水果名称")
    print(f"结果类型: {type(result1)}")
    print(f"结果: {result1}")
    print(f"是否为 None: {result1 is None}")

    # 测试2: 查看原始响应
    print("\n=== 测试2: 原始 LLM 响应 ===")
    raw_result = llm.invoke(
        "创建一个名为'test'的对象，包含3个水果名称。请使用JSON格式: {name: str, items: List[str]}"
    )
    print(f"原始响应类型: {type(raw_result)}")
    print(f"内容: {raw_result.content}")
    if hasattr(raw_result, "additional_kwargs"):
        print(f"额外参数: {raw_result.additional_kwargs}")

    # 测试3: 带明确 schema 的 function calling
    print("\n=== 测试3: 检查 function_calling 配置 ===")
    from langchain_core.utils.function_calling import convert_to_openai_function

    openai_func = convert_to_openai_function(SimpleTest)
    print(f"OpenAI Function Schema:")
    print(json.dumps(openai_func, indent=2))

    # 测试4: 手动绑定函数
    print("\n=== 测试4: 手动绑定函数调用 ===")
    llm_with_tools = llm.bind_tools([SimpleTest])
    result4 = llm_with_tools.invoke("创建一个名为'test'的对象，包含3个水果名称")
    print(f"结果类型: {type(result4)}")
    print(f"内容: {result4.content}")
    if hasattr(result4, "tool_calls"):
        print(f"Tool calls: {result4.tool_calls}")
    if hasattr(result4, "additional_kwargs"):
        print(f"额外参数键: {result4.additional_kwargs.keys()}")
        if "tool_calls" in result4.additional_kwargs:
            print(f"Tool calls 内容:")
            for tc in result4.additional_kwargs["tool_calls"]:
                print(f"  - {tc}")


if __name__ == "__main__":
    test_structured_output()
