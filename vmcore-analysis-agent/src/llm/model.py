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

    try:
        llm = ChatOpenAI(
            api_key=str(api_key),
            base_url=str(base_url),
            model=str(model_name),
            temperature=float(
                temperature
            ),  # https://api-docs.deepseek.com/zh-cn/quick_start/parameter_settings
        )
        logger.info(f"Successfully created LLM instance, model name: {model_name}")
        return llm
    except Exception as e:
        logger.error(f"Failed to create LLM instance: {e}")
        raise
