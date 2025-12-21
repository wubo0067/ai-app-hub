import os
from langchain_tavily import TavilySearch
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.messages import SystemMessage, HumanMessage


def main():
    print("Hello from tavily-test!")
    search_tool = TavilySearch(max_results=3)
    tools = [search_tool]
    # result = search_tool.run("DeepSeek 最新发布了什么模型")
    # print(result)

    llm = ChatOpenAI(
        api_key="sk-b5480f840a794c69a0af1732459f3ae4",  # type: ignore
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
    )

    prompt = "You are a helpful assistant."

    agent_executor = create_agent(llm, tools, system_prompt=prompt)

    result = agent_executor.invoke(
        {"messages": [HumanMessage("who is the winnner of the us open")]}
    )
    print(result)


if __name__ == "__main__":
    os.environ["TAVILY_API_KEY"] = "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"
    main()
