__import__("pysqlite3")
import sys

sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")

import os

# 1. 获取密钥并校验
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")
if not DEEPSEEK_KEY:
    raise ValueError(
        "未设置 DEEPSEEK_API_KEY！请在终端执行：export DEEPSEEK_API_KEY=sk-xxx"
    )

# 2. 🔑 关键修复：兼容 langchain_openai 底层的环境变量检查
os.environ["OPENAI_API_KEY"] = DEEPSEEK_KEY

from langchain_openai import ChatOpenAI
from crewai import Agent, Task, Crew, Process

# --- 配置 ---
# 确保环境变量已设置 API 密钥（如 GOOGLE_API_KEY）
try:
    llm = ChatOpenAI(
        api_key=DEEPSEEK_KEY,
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0.1,  # temperature 的作用是控制生成文本的随机性，值越低，生成的文本越确定和一致
    )
    print(f"语言模型初始化成功：{llm.model}")
except Exception as e:
    print(f"语言模型初始化失败：{e}")
    llm = None


def main():
    print("Hello from adp-07-multiagent!")

    # Define Agents with specific roles, goals, and backstories
    researcher = Agent(
        role="Senior Research Analyst",
        goal="Find and summarize the latest trends in AI.",
        backstory="You are an experienced research analyst with a\nknack for identifying key trends and synthesizing information.",
        verbose=True,
        allow_delegation=False,
    )

    writer = Agent(
        role="Technical Content Writer",
        goal="Write a clear and engaging blog post abased on research findings.",
        backstory="You are a skilled writer who can translate complex technical topics into accessible content.",
        verbose=True,
        allow_delegation=False,
    )

    # Define tasks for the agents
    research_task = Task(
        description="Research the top 3 emerging trends in Artificial Intelligence in 2024-2025. Focus on practical applications and potential impact.",
        expected_output="A detailed summary of the top 3 AI trends,\nincluding key points and sources.",
        agent=researcher,
    )

    writing_task = Task(
        description="Write a 200-word blog post based on the research findings. The post should be engaging and easy for a general audience to understand.",
        expected_output="A complete 200-word blog post about the latest AI trends.",
        agent=writer,
        context=[research_task],
    )

    # Create the crew
    blog_creation_crew = Crew(
        agents=[researcher, writer],
        tasks=[research_task, writing_task],
        llm=llm,
        verbose=True,
    )

    # Execute the Crew
    print("## Running the blog creation crew with DeepSeek... ##")
    try:
        result = blog_creation_crew.kickoff()
        print("\n----------------------\n")
        print("## Crew Final Output ##")
        print(result)
    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
