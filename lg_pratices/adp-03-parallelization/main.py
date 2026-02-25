import os
import asyncio
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import (
    Runnable,
    RunnableParallel,
    RunnablePassthrough,
    RunnableConfig,
    chain,
)


try:
    llm = ChatNVIDIA(
        # model="moonshotai/kimi-k2.5",
        model="z-ai/glm4.7",
        api_key=os.getenv("NVIDIA_API_KEY"),
        temperature=1,
        top_p=1,
        max_tokens=16384,
        extra_body={
            "chat_template_kwargs": {"enable_thinking": False, "clear_thinking": True}
        },
    )
    print(f"语言模型初始化成功：{llm.model}")
except Exception as e:
    print(f"语言模型初始化失败：{e}")
    llm = None

# --- Define Independent Chains ---
# 独立工作的三条链
summarize_chain: Runnable = (
    ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "请简明扼要地总结以下主题：",
            ),
            ("user", "{topic}"),
        ]
    )
    | llm
    | StrOutputParser()
)

questions_chain: Runnable = (
    ChatPromptTemplate.from_messages(
        [("system", "请针对以下主题生成三个有趣的问题："), ("user", "{topic}")]
    )
    | llm
    | StrOutputParser()
)

terms_chain: Runnable = (
    ChatPromptTemplate.from_messages(
        [
            ("system", "请从以下主题中提取 5-10 个关键词，用逗号分隔："),
            ("user", "{topic}"),
        ]
    )
    | llm
    | StrOutputParser()
)

# --- 构建并行 + 汇总链 ---
map_chain = RunnableParallel(
    {
        "summary": summarize_chain,
        "questions": questions_chain,
        "key_terms": terms_chain,
        "topic": RunnablePassthrough(),  # 传递原始 topic
    }
)

# 2. 定义最终汇总 prompt，整合并行结果
synthesis_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """根据以下信息：
    摘要：{summary}
    相关问题：{questions}
    关键词：{key_terms}
    请综合生成完整答案。""",
        ),
        ("user", "原始主题：{topic}"),
    ]
)

# 3. 构建完整链，将并行结果直接传递给汇总 prompt，再由 LLM 和输出解析器处理
full_parallel_chain = map_chain | synthesis_prompt | llm | StrOutputParser()


# --- 方式 1: 通过继承 Runnable 类自定义组件 ---
# 这种方式适合需要维护状态（如 __init__ 参数）或复杂逻辑的组件
class AddSuffixRunnable(Runnable):
    def __init__(self, suffix: str):
        self.suffix = suffix

    # 必须实现 invoke 方法
    def invoke(self, input: str, config: RunnableConfig = None) -> str:
        return f"{input} {self.suffix}"


# --- 方式 2: 使用 @chain 装饰器 ---
# 这种方式适合将简单的函数快速转换为 Runnable
@chain
def count_length(input: str) -> str:
    return f"Length of text: {len(input)}"


async def run_parallel_example(topic: str) -> None:
    """
    异步调用并行处理链，输出综合结果。

    Args:
        topic: 传递给 LangChain 的主题输入
    """
    if not llm:
        print("LLM 未初始化，无法运行示例。")
        return

    print(f"\n--- 并行 LangChain 示例，主题：'{topic}' ---")
    try:
        # `ainvoke` 的输入是单个 topic 字符串，
        # 会传递给 map_chain 中的每个 runnable
        response = await full_parallel_chain.ainvoke(topic)
        print("\n--- 最终响应 ---")
        print(response)
    except Exception as e:
        print(f"\n链执行出错：{e}")


def main():
    print("Hello from adp-03-parallelization!")

    # 1. 实例化自定义 Runnable
    my_suffix_component = AddSuffixRunnable(suffix="[END]")

    # 2. 如何检查一个对象是否是 Runnable？
    # 使用 isinstance 检查是否继承自 langchain_core.runnables.Runnable
    print(f"\n--- 类型检查 ---")
    print(f"summarize_chain is Runnable? {isinstance(summarize_chain, Runnable)}")
    print(
        f"my_suffix_component is Runnable? {isinstance(my_suffix_component, Runnable)}"
    )
    print(f"count_length is Runnable? {isinstance(count_length, Runnable)}")

    # 3. 实际运行
    if llm:
        print(f"\n--- 执行自定义组件 ---")
        # 我们可以像乐高积木一样把它们串起来
        # 流程：总结 -> 加后缀 -> 计算长度
        full_chain = summarize_chain | my_suffix_component | count_length

        input_topic = "LangChain 的 Runnable 协议"
        print(f"Input: {input_topic}")
        result = full_chain.invoke({"topic": input_topic})
        print(f"Result: {result}")

        # --- 演示默认方法 ---
        # 虽然 AddSuffixRunnable 只实现了 invoke，但它自动获得了 batch 和 stream 能力
        print(f"\n--- 测试默认实现的 batch 方法 ---")
        inputs = ["Hello", "World", "LangChain"]
        # Runnable 默认使用线程池并发调用 invoke 来实现 batch
        batch_results = my_suffix_component.batch(inputs)
        print(f"Batch results: {batch_results}")

        print(f"\n--- 测试默认实现的 stream 方法 ---")
        # Runnable 默认的 stream 只是简单地 yield invoke 的返回值
        print("Stream output: ", end="")
        for chunk in my_suffix_component.stream("Stream Me"):
            print(f"[{chunk}]", end="")
        print()


if __name__ == "__main__":
    asyncio.run(run_parallel_example("人工智能在医疗领域的应用"))
    main()
