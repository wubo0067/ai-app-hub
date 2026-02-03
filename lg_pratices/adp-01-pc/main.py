import os
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# 推荐用 .env 文件加载环境变量
# from dotenv import load_dotenv
# load_dotenv()
# 确保 OPENAI_API_KEY 已在 .env 文件中设置

# 初始化语言模型（推荐使用 ChatOpenAI）
llm = ChatOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
    model="deepseek-chat",
    temperature=0,  # temperature 的作用是控制生成文本的随机性，值越低，生成的文本越确定和一致
)

# --- 提示 1：信息提取 ---
prompt_extract = ChatPromptTemplate.from_template(
    "请从以下文本中提取技术规格：\n\n{text_input}"
)

# --- 提示 2：转为 JSON ---
prompt_transform = ChatPromptTemplate.from_template(
    "请将以下技术规格转为 JSON 格式，包含 'cpu'、'memory' 和 'storage' 三个键：\n\n{specifications}"
)

# --- 用 LCEL 构建链 ---
# StrOutputParser() 将 LLM 消息输出转为字符串
extraction_chain = prompt_extract | llm | StrOutputParser()

# 全链将提取链的输出作为 'specifications' 变量传递给转换提示
full_chain = (
    {"specifications": extraction_chain} | prompt_transform | llm | StrOutputParser()
)

# --- 运行链 ---
input_text = "新款笔记本配备 3.5GHz 八核处理器、16GB 内存和 1TB NVMe SSD。"

# 用输入文本字典执行链
final_result = full_chain.invoke({"text_input": input_text})

print("\n--- 最终 JSON 输出 ---")
print(final_result)
