import os
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableBranch

# --- 配置 ---
# 确保环境变量已设置 API 密钥（如 GOOGLE_API_KEY）
try:
    llm = ChatOpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0,  # temperature 的作用是控制生成文本的随机性，值越低，生成的文本越确定和一致
    )
    print(f"语言模型初始化成功：{llm.model}")
except Exception as e:
    print(f"语言模型初始化失败：{e}")
    llm = None

# --- 定义模拟子智能体处理器（等同于 ADK sub_agents） ---


def booking_handler(request: str) -> str:
    """模拟预订智能体处理请求。"""
    print("\n--- 委托给预订处理器 ---")
    return f"预订处理器已处理请求：'{request}'。结果：模拟预订动作。"


def info_handler(request: str) -> str:
    """模拟信息智能体处理请求。"""
    print("\n--- 委托给信息处理器 ---")
    return f"信息处理器已处理请求：'{request}'。结果：模拟信息检索。"


def unclear_handler(request: str) -> str:
    """处理无法委托的请求。"""
    print("\n--- 处理不明确请求 ---")
    return f"协调者无法委托请求：'{request}'。请补充说明。"


# --- 定义协调者路由链（等同于 ADK 协调者指令） ---
coordinator_router_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """分析用户请求，判断应由哪个专属处理器处理。
    - 若请求涉及预订机票或酒店，输出 'booker'。
    - 其他一般信息问题，输出 'info'。
    - 若请求不明确或不属于上述类别，输出 'unclear'。
    只输出一个词：'booker'、'info' 或 'unclear'。""",
        ),
        ("user", "{request}"),
    ]
)

if llm:
    coordinator_router_chain = coordinator_router_prompt | llm | StrOutputParser()

# --- 定义委托逻辑（等同于 ADK 的 Auto-Flow） ---
branches = {
    "booker": RunnablePassthrough.assign(
        # 在 x 上添加一个字段 output
        output=lambda x: booking_handler(x["request"]["request"])
    ),
    "info": RunnablePassthrough.assign(
        output=lambda x: info_handler(x["request"]["request"])
    ),
    "unclear": RunnablePassthrough.assign(
        output=lambda x: unclear_handler(x["request"]["request"])
    ),
}

delegation_branch = RunnableBranch(
    (
        lambda x: x["decision"].strip() == "booker",
        branches["booker"],
    ),  # 它选择执行 branches["booker"]。
    (lambda x: x["decision"].strip() == "info", branches["info"]),
    branches["unclear"],  # 默认分支
)
# 这一步的最终输出是
# {
#     "decision": "booker",
#     "request": {"request": "帮我..."},
#     "output": "预订处理器已处理请求..."  # <-- 这是 assign 新增的字段
# }

# 这段代码使用了 LangChain 的声明式语法（LCEL）来构建一个链（Chain），这个链就是所谓的“agent”对象。
# 简单地说，在 LangChain 中，任何实现了 Runnable 协议的对象（包括 Prompt、LLM、OutputParser
# 以及通过 | 连接的组合）都具备 invoke 方法。

# 在 Python 中，LangChain 重载了按位或运算符 |。当你写 A | B 时，它实际上并没有立即执行，
# 而是创建了一个新的 RunnableSequence（可运行序列）对象。
coordinator_agent = (
    # 代码的第一部分是一个字典，这在 LCEL 中被称为 RunnableParallel。它会同时把相同的输入传给字典里的每一个 value。
    # 会调用 RunnablePassthrough.invoke({"request": request_a})
    # "request": {"request": "帮我预订飞往伦敦的机票。"}
    {"decision": coordinator_router_chain, "request": RunnablePassthrough()}
    | delegation_branch
    | (lambda x: x["output"])
)


# --- 示例用法 ---
def main():
    if not llm:
        print("\n因 LLM 初始化失败，跳过执行。")
        return

    print("--- 预订请求示例 ---")
    request_a = "帮我预订飞往伦敦的机票。"
    result_a = coordinator_agent.invoke({"request": request_a})
    print(f"最终结果 A: {result_a}")

    print("\n--- 信息请求示例 ---")
    request_b = "意大利的首都是哪里？"
    result_b = coordinator_agent.invoke({"request": request_b})
    print(f"最终结果 B: {result_b}")

    print("\n--- 不明确请求示例 ---")
    request_c = "讲讲量子物理。"
    result_c = coordinator_agent.invoke({"request": request_c})
    print(f"最终结果 C: {result_c}")


if __name__ == "__main__":
    main()
