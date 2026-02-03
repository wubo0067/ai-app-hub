import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent

# 加载模型配置
_ = load_dotenv()

# 配置大模型服务
llm = ChatOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
    model="deepseek-chat",
)

# 创建 Agent
agent = create_agent(model=llm)


# langgraph-cli 入口函数
def get_app():
    return agent
