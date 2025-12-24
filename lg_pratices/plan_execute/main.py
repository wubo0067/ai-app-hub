import os
from langchain_tavily import TavilySearch
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langgraph.prebuilt import create_react_agent
from langchain.messages import SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import StateGraph, START, END
from typing import TypedDict, List, Tuple, Annotated, Literal, Union
from pydantic import BaseModel, Field
import operator


# 定义 State, 用于存储输入，计划，过去的步骤和响应
class PlanExecute(TypedDict):
    input: str  # 用户输入
    plan: List[str]  # 计划步骤列表，这个步骤会被 replanner 更新
    past_steps: Annotated[
        List[Tuple], operator.add
    ]  # 记录先前执行的步骤，每个元组包含执行步骤和执行结果
    response: str  # 最终响应结果


# 定义计划步骤，用于描述未来要执行的计划，第一阶段用户输入问题，llm 针对该问题分解的步骤，llm 的 response 会结构化输出到 Plan
class Plan(BaseModel):
    """Plan to follow in future"""

    steps: List[str] = Field(
        description="different steps to follow, should be in sorted order"
    )


class Response(BaseModel):
    """Response to the user's query"""

    response: str = Field(description="answer to the user's query")


class Act(BaseModel):
    """Action to be taken by the agent"""

    action: Union[Response, Plan] = Field(
        description="Action to perform. If you want to respond to user, use Response. "
        "If you need to further use tools to get the answer, use Plan."
    )


# 生成计划函数
async def plan_step(state: PlanExecute) -> PlanExecute:
    """Generate a plan based on the input"""
    # 构造消息列表，包含用户输入
    messages = [HumanMessage(content=state["input"])]
    # 调用 planner 生成计划，planner 会结构化输出到 Plan 对象的 steps
    plan_result = await planner.ainvoke({"messages": messages})
    # 获取结构化输出的 steps
    plan_steps = plan_result.steps
    print(f"Generated plan steps: {plan_steps}")
    # 将 plan_steps 放入 state 的 plan 字段中返回
    return {"plan": plan_steps}


# 步骤执行函数
async def execute_step(state: PlanExecute) -> PlanExecute:
    """Execute a single step of the plan"""
    # 从状态中获取计划列表，例如：["检查 CPU 使用率", "分析进程列表", "检查系统日志"]
    plan = state["plan"]
    # 2. 格式化计划为编号列表字符串
    plan_str = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(plan))
    print(f"--->will execute plan: {plan_str}")
    # 3. 获取当前要执行的任务（第一个），例如："检查 CPU 使用率"
    task = plan[0]

    # 4. 构造任务提示词
    task_formatted = f"""For the following plan:
{plan_str}\n\nYou are tasked with executing step {1}, {task}."""

    # 5. 异步调用 agent 执行任务
    messages = [HumanMessage(content=task_formatted)]
    # 这个 invoke 就包括了工具调用，调用 tavily_search
    agent_response = await agent_executor.ainvoke({"messages": messages})
    # !!agent_response 返回的是一个 dict，messages 字段必有，包含整个执行过程中交换的所有消息列表
    # {
    #    "messages": [
    #        HumanMessage(content="..."),      # 你的输入
    #        AIMessage(content="...", tool_calls=[...]),  # AI 的思考 + 工具调用
    #        ToolMessage(content="...", tool_call_id="..."),  # 工具执行结果
    #        AIMessage(content="...")           # AI 的最终回答
    #    ]
    # }
    print(f"<---execute response: {agent_response}")
    # 记录过往步骤和结果
    return {
        # 拿最终追加的最后一条消息（通常是最后一轮 AIMessage）作为本 step 的结果
        "past_steps": [(task, agent_response["messages"][-1].content)],
    }


# 重新规划步骤函数
async def replan_step(state: PlanExecute) -> PlanExecute:
    # replanner 返回 Act 结构对象，该结构包含 Response, Plan
    # !! AI 会根据 Prompt 返回新的 Plan，或者直接返回 Response, Action 是个 Union
    output = await replanner.ainvoke(state)
    print(f"<---replanner output:", output)
    if isinstance(output.action, Response):
        print("<---replanner final response:", output.action.response)
        return {"response": output.action.response}
    else:
        print("<---replanner updated plan steps:", output.action.steps)
        return {"plan": output.action.steps}


def should_end(state: PlanExecute):

    if "response" in state and state["response"]:
        # 返回 END：当 state 中存在 response 且不为空时，流程结束
        return END
    else:
        # 否则继续执行 agent 节点，继续处理计划中的下一步
        return "agent"


async def main():
    # 开始创建 Graph
    workflow = StateGraph(PlanExecute)

    # Add the plan node
    workflow.add_node("planner", plan_step)

    # Add the execution step, agent 包括 LLM 调用和工具使用
    # agent 是思考 + 行动，所以 agent 应该有两个步骤
    # !! 1：给出问题，agent 思考后给出行动方案
    # !! 2：根据行动方案使用工具，给出结果
    workflow.add_node("agent", execute_step)

    # Add a replan node
    workflow.add_node("replan", replan_step)

    # 第一步构建计划
    workflow.add_edge(START, "planner")

    # 从计划到执行计划中的 steps,
    workflow.add_edge("planner", "agent")

    # From agent, we replan
    workflow.add_edge("agent", "replan")

    # 根据 should_end 函数的判断，决定是继续执行 agent 还是结束
    workflow.add_conditional_edges(
        "replan",
        # Next, we pass in the function that will determine which node is called next.
        should_end,
        ["agent", END],
    )

    app = workflow.compile()

    config = {"recursion_limit": 50}
    # inputs = {"input": "what is the hometown of the mens 2024 Australia open winner?"}
    inputs = {
        "input": "Where is the hometown of the Asian men's 100-meter sprint record holder?"
    }
    async for event in app.astream(inputs, config=config):
        for k, v in event.items():
            if k != "__end__":
                print(v)


if __name__ == "__main__":
    # 先设置环境变量
    os.environ["TAVILY_API_KEY"] = "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"
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = "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" # fmt: skip

    # 在环境变量设置后初始化工具和模型
    search_tool = TavilySearch(max_results=1)
    tools = [search_tool]

    llm = ChatOpenAI(
        api_key="sk-b5480f840a794c69a0af1732459f3ae4",  # type: ignore
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0,  # temperature 的作用是控制生成文本的随机性，值越低，生成的文本越确定和一致
    )

    prompt = "You are a helpful assistant."
    # 创建全局 agent_executor
    global agent_executor, planner, replanner
    agent_executor = create_agent(llm, tools, system_prompt=prompt)

    # 定义用于生成计划的提示模板
    planner_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """For the given objective, come up with a simple step by step plan. \
        This plan should involve individual tasks, that if executed correctly will yield the correct answer. \
    D   o not add any superfluous steps. \
        The result of the final step should be the final answer. \
        Make sure that each step has all the information needed - do not skip steps.""",
            ),
            ("placeholder", "{messages}"),
        ]
    )
    # Plan 是提示大模型要结构化输出
    # !! 为什么大模型对 planner_prompt 这样的 prompt 产生的 response 能解析成 Plan 这种 schema 呢？
    # !! 模型是如何理解“结构”与“语义”映射的核心机制
    # !! LangChain 的 structured output ≠ 纯 prompt 工程，而是模型能力 + 协议约束 + 解析器三者协同工作的结果。
    # ** with_structured_output(schema) 里的 schema 并不是简单地“加到 prompt 里的说明文字”。LangChain 把 schema 转换成一种「模型必须遵守的输出协议（contract）」，
    # ** 并通过 OpenAI function calling / JSON mode / tool calling 等机制，强制模型只输出可被该 schema 解析的结构化数据。
    # ** schema / function 信息也会被发送给大模型，但它们不是作为普通的 prompt 文本发送的，而是作为「模型调用协议的一部分」发送的。
    planner = planner_prompt | llm.with_structured_output(
        Plan, method="function_calling"
    )

    # 定义用于重新规划的提示模板
    replanner_prompt = ChatPromptTemplate.from_template(
        """For the given objective, come up with a simple step by step plan. \
        This plan should involve individual tasks, that if executed correctly will yield the correct answer.,
        Do not add any superfluous steps. The result of the final step should be the final answer. ,
        Make sure that each step has all the information needed - do not skip steps.

        Your objective was this:
        {input}

        Your original plan was this:
        {plan}

        You have currently done the follow steps:
        {past_steps}

        Update your plan accordingly. \
        If no more steps are needed and you can return to the user, then respond with that. \
        Otherwise, fill out the plan. Only add steps to the plan that still NEED to be done. \
        Do not return previously done steps as part of the plan."""
    )
    replanner = replanner_prompt | llm.with_structured_output(
        Act, method="function_calling"
    )

    # 运行主程序
    import asyncio

    asyncio.run(main())
