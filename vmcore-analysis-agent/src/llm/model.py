import os
from typing import Any

# from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage
from src.mcp_tools.crash.client import crash_tools
from src.utils.config import config_manager
from src.utils.logging import logger
from langchain_deepseek.chat_models import ChatDeepSeek


class ChatDeepSeekReasoner(ChatDeepSeek):
    """ChatDeepSeek 子类，修复 reasoning_content 在多轮对话中丢失的问题。

    langchain-deepseek 在接收响应时会将 reasoning_content 存入
    AIMessage.additional_kwargs，但在发送请求时 _convert_message_to_dict
    不会将其写回序列化后的消息字典。DeepSeek-Reasoner API 要求多轮对话中
    每条 assistant 消息都必须包含 reasoning_content 字段，否则返回 400 错误。
    """

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        # 在序列化前获取原始 BaseMessage 列表
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        # 将 reasoning_content 注入到序列化后的 assistant 消息中
        if "messages" in payload:
            ai_msg_index = 0
            for orig_msg in messages:
                if isinstance(orig_msg, AIMessage):
                    reasoning = orig_msg.additional_kwargs.get("reasoning_content")
                    # 在 payload["messages"] 中找到对应的 assistant 消息
                    while ai_msg_index < len(payload["messages"]):
                        if payload["messages"][ai_msg_index].get("role") == "assistant":
                            if reasoning is not None:
                                payload["messages"][ai_msg_index][
                                    "reasoning_content"
                                ] = reasoning
                            ai_msg_index += 1
                            break
                        ai_msg_index += 1

        return payload


def create_llm():
    """Create and return ChatOpenAI instance"""
    api_key = config_manager.get("DEEPSEEK_API_KEY")
    base_url = config_manager.get("BASE_URL")
    model_name = config_manager.get("LLM_MODEL")
    temperature = float(config_manager.get("TEMPERATURE"))

    if not all([api_key, base_url, model_name]):
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
            top_p=0.9,
            temperature=temperature,  # https://api-docs.deepseek.com/zh-cn/quick_start/parameter_settings
        )
        logger.info(f"Successfully created LLM instance, model: {llm}")
        return llm
    except Exception as e:
        logger.error(f"Failed to create LLM instance: {e}")
        raise
