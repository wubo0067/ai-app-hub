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

    # 测试 1: 简单的结构化输出
    print("=== 测试 1: 简单结构化输出 ===")
    simple_chain = llm.with_structured_output(SimpleTest, method="function_calling")
    result1 = simple_chain.invoke("创建一个名为'test'的对象，包含 3 个水果名称")
    print(f"结果类型：{type(result1)}")
    print(f"结果：{result1}")
    print(f"是否为 None: {result1 is None}")

    # 测试 2: 查看原始响应
    print("\n=== 测试 2: 原始 LLM 响应 ===")
    raw_result = llm.invoke(
        "创建一个名为'test'的对象，包含 3 个水果名称。请使用 JSON 格式：{name: str, items: List[str]}"
    )
    print(f"原始响应类型：{type(raw_result)}")
    print(f"内容：{raw_result.content}")
    if hasattr(raw_result, "additional_kwargs"):
        print(f"额外参数：{raw_result.additional_kwargs}")

    # 测试 3: 带明确 schema 的 function calling
    print("\n=== 测试 3: 检查 function_calling 配置 ===")
    from langchain_core.utils.function_calling import convert_to_openai_function

    openai_func = convert_to_openai_function(SimpleTest)
    print(f"OpenAI Function Schema:")
    print(json.dumps(openai_func, indent=2))

    # 测试 4: 手动绑定函数
    print("\n=== 测试 4: 手动绑定函数调用 ===")
    llm_with_tools = llm.bind_tools([SimpleTest])
    result4 = llm_with_tools.invoke("创建一个名为'test'的对象，包含 3 个水果名称")
    print(f"结果类型：{type(result4)}")
    print(f"内容：{result4.content}")
    if hasattr(result4, "tool_calls"):
        print(f"Tool calls: {result4.tool_calls}")
    if hasattr(result4, "additional_kwargs"):
        print(f"额外参数键：{result4.additional_kwargs.keys()}")
        if "tool_calls" in result4.additional_kwargs:
            print(f"Tool calls 内容：")
            for tc in result4.additional_kwargs["tool_calls"]:
                print(f"  - {tc}")


if __name__ == "__main__":
    test_structured_output()
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = str(config_manager.get("LANGSMITH_API_KEY"))

    # top_p 值	采样集合大小	随机性	确定性	适合场景
    # top_p=0.1	很小	很低	很高	代码生成、事实回答
    # top_p=0.5	中等	中等	中等	创意写作、头脑风暴
    # top_p=0.9	较大	较高	较低	探索性分析、多样化输出
    # top_p=1.0	全部词汇	最高	最低	开放式创作

    try:
        # 使用 ChatDeepSeekReasoner 以修复 reasoning_content 在多轮对话中丢失的问题
        llm = ChatDeepSeekReasoner(
            api_key=str(api_key),
            base_url=str(base_url),
            model=str(model_name),
            max_tokens=(
                48000
                if "think" in str(model_name) or "reasoner" in str(model_name)
                else 8000
            ),  # DeepSeek-Reasoner 模式需要更大的 max_tokens 来支持长对话历史和复杂推理
            top_p=0.85,  #
            presence_penalty=0,  # 不需要模型通过增加多样性来“换个说法”，我们需要的是精确的原始符号。
            temperature=temperature,  # https://api-docs.deepseek.com/zh-cn/quick_start/parameter_settings            timeout=300,  # 5 分钟超时，后期步骤对话历史很长，LLM 推理耗时较久
            max_retries=3,  # 遇到连接超时等瞬态错误时自动重试
        )
        logger.info(f"Successfully created LLM instance, model: {llm}")
        return llm
    except Exception as e:
        logger.error(f"Failed to create LLM instance: {e}")
        raise


def create_chat_llm():
    """Create a ChatDeepSeek instance with deepseek-chat model.

    用于将 DeepSeek-Reasoner 的纯文本 reasoning_content 结构化为 JSON。
    当 Reasoner 模型返回空 content 但有 reasoning_content 时，
    使用此 Chat 模型将推理内容转换为 VMCoreAnalysisStep 结构化输出。
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = config_manager.get("BASE_URL")

    if not api_key or not base_url:
        logger.error("Missing DEEPSEEK_API_KEY env var or BASE_URL for chat LLM")
        raise ValueError("Missing DEEPSEEK_API_KEY env var or BASE_URL for chat LLM")

    try:
        llm = ChatDeepSeek(
            api_key=str(api_key),
            base_url=str(base_url),
            model="deepseek-chat",
            max_tokens=8000,
            top_p=0.1,
            temperature=0.0,
            timeout=120,
            max_retries=3,
        )
        logger.info(f"Successfully created chat LLM instance: {llm}")
        return llm
    except Exception as e:
        logger.error(f"Failed to create chat LLM instance: {e}")
        raise
