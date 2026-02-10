import os
from langchain_openai import ChatOpenAI
from src.mcp_tools.crash.client import crash_tools
from src.utils.config import config_manager
from src.utils.logging import logger


def create_llm():
    """Create and return ChatOpenAI instance"""
    api_key = config_manager.get("DEEPSEEK_API_KEY")
    base_url = config_manager.get("BASE_URL")
    model_name = config_manager.get("LLM_MODEL")
    temperature = config_manager.get("TEMPERATURE")

    if not all([api_key, base_url, model_name, temperature]):
        logger.error("Missing required LLM configuration parameters")
        raise ValueError("Missing required LLM configuration parameters")

    # 配置 LangSmith 追踪
    if config_manager.get("LANGSMITH_TRACING"):
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = str(config_manager.get("LANGSMITH_API_KEY"))

    # top_p 值	采样集合大小	随机性	确定性	适合场景
    # top_p=0.1	很小	很低	很高	代码生成、事实回答
    # top_p=0.5	中等	中等	中等	创意写作、头脑风暴
    # top_p=0.9	较大	较高	较低	探索性分析、多样化输出
    # top_p=1.0	全部词汇	最高	最低	开放式创作

    try:
        llm = ChatOpenAI(
            api_key=str(api_key),
            base_url=str(base_url),
            model=str(model_name),
            max_tokens=8000,
            top_p=0.9,
            temperature=float(
                temperature
            ),  # https://api-docs.deepseek.com/zh-cn/quick_start/parameter_settings
        )
        logger.info(f"Successfully created LLM instance, model name: {llm.model_name}")
        return llm
    except Exception as e:
        logger.error(f"Failed to create LLM instance: {e}")
        raise
