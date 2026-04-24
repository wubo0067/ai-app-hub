#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# model.py - LLM 模型配置和初始化模块
# Author: CalmWU
# Created: 2026-01-09

import os
from typing import Any
from pydantic import SecretStr

# from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage
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


def create_reasoning_llm():
    """Create and return the reasoning LLM instance."""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = str(
        config_manager.get("base_url")
    ).lower()  # DeepSeek API 的 base_url 配置项
    # 是否是 think 模式
    thinking = str(config_manager.get("thinking", "disabled")).lower()
    # 模型名称
    model_name = str(config_manager.get("llm_model")).lower()
    temperature = float(config_manager.get("temperature"))
    reasoning_effort = str(config_manager.get("reasoning_effort", "max")).lower()

    if not all([api_key, base_url, model_name]):
        logger.error("Missing required LLM configuration parameters")
        raise ValueError("Missing required LLM configuration parameters")

    # 配置 LangSmith 追踪
    if config_manager.get("langsmith_tracing"):
        os.environ["LANGSMITH_TRACING"] = "true"

    # top_p 值      采样集合大小    随机性        确定性        适合场景
    # top_p=0.1     很小           很低          很高          代码生成、事实回答
    # top_p=0.5     中等           中等          中等          创意写作、头脑风暴
    # top_p=0.9     较大           较高          较低          探索性分析、多样化输出
    # top_p=1.0     全部词汇       最高          最低          开放式创作

    try:
        if thinking == "enabled":
            # 使用的是思考模式
            llm_class = ChatDeepSeekReasoner
            max_tokens = 48000  # Reasoner 模型的 max_tokens 同时包含 reasoning_tokens + content_tokens，需要足够大
            llm_kwargs = {
                "reasoning_effort": reasoning_effort,
                "extra_body": {"thinking": {"type": "enabled"}},
            }
        else:
            # 普通对话模式
            llm_class = ChatDeepSeek
            max_tokens = 8192
            llm_kwargs = {
                "top_p": 0.1,  # 使用低随机性设置，适合代码生成和事实回答
                "presence_penalty": 0,  # 保持输出更贴近原始符号和事实
                "temperature": temperature,  # https://api-docs.deepseek.com/zh-cn/quick_start/parameter_settings
                "extra_body": {"thinking": {"type": "disabled"}},
            }

        llm = llm_class(
            api_key=SecretStr(str(api_key)),
            base_url=base_url,
            model=model_name,
            max_tokens=max_tokens,
            timeout=300,  # 5 分钟超时，后期步骤对话历史很长，LLM 推理耗时较久
            max_retries=3,  # 遇到连接超时等瞬态错误时自动重试
            **llm_kwargs,
        )
        logger.info(f"Successfully created LLM instance, model: {llm}")
        return llm
    except Exception as e:
        logger.error(f"Failed to create LLM instance: {e}")
        raise


def create_structured_llm():
    """Create the structured-output LLM instance.

    用于将 DeepSeek-Reasoner 的纯文本 reasoning_content 结构化为 JSON。
    当 Reasoner 模型返回空 content 但有 reasoning_content 时，
    使用此 Chat 模型将推理内容转换为 VMCoreAnalysisStep 结构化输出。
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = str(
        config_manager.get("base_url")
    ).lower()  # DeepSeek API 的 base_url 配置项

    # 模型名称
    model_name = str(config_manager.get("llm_model")).lower()

    if not api_key or not base_url:
        logger.error("Missing DEEPSEEK_API_KEY env var or BASE_URL for chat LLM")
        raise ValueError("Missing DEEPSEEK_API_KEY env var or BASE_URL for chat LLM")

    try:
        llm = ChatDeepSeek(
            api_key=SecretStr(str(api_key)),
            base_url=base_url,
            model=model_name,
            max_tokens=8192,
            top_p=0.1,
            temperature=0.0,
            timeout=120,
            max_retries=3,
            extra_body={"thinking": {"type": "disabled"}},
        )
        logger.info(f"Successfully created chat LLM instance: {llm}")
        return llm
    except Exception as e:
        logger.error(f"Failed to create chat LLM instance: {e}")
        raise
