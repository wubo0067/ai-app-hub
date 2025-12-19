from langchain.messages import SystemMessage
from tools import model_with_tools
from log import logger


# The model node is used to call the LLM and decide whether to call a tool or not.


def llm_call(state: dict) -> dict:
    """LLM decides whether to call a tool or not"""

    # 构建要发送给 LLM 的消息列表
    messages_to_send = [
        SystemMessage(
            content="You are a helpful assistant tasked with performing arithmetic on a set of inputs."
        )
    ] + state["messages"]

    # 记录调用参数
    logger.debug("=" * 80)
    logger.debug("LLM Node - 准备调用 model_with_tools.invoke")
    logger.debug(f"当前 LLM 调用次数：{state.get('llm_calls', 0)}")
    logger.debug(f"消息数量：{len(messages_to_send)}")

    for idx, msg in enumerate(messages_to_send):
        logger.debug(f"消息 [{idx}] - 类型：{type(msg).__name__}")
        logger.debug(
            f"消息 [{idx}] - 内容：{msg.content if hasattr(msg, 'content') else msg}"
        )
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            logger.debug(f"消息 [{idx}] - 工具调用：{msg.tool_calls}")

    # 调用 LLM
    logger.info("正在调用 LLM...")
    try:
        response = model_with_tools.invoke(messages_to_send)

        # 记录返回信息
        logger.debug("-" * 80)
        logger.debug("LLM 响应详情：")
        logger.debug(f"响应类型：{type(response).__name__}")
        logger.debug(f"响应内容：{response}")

        if hasattr(response, "tool_calls") and response.tool_calls:
            logger.info(f"LLM 决定调用 {len(response.tool_calls)} 个工具")
            for idx, tool_call in enumerate(response.tool_calls):
                logger.debug(f"工具调用 [{idx}]:")
                logger.debug(f"  - 工具名称：{tool_call.get('name', 'N/A')}")
                logger.debug(f"  - 工具参数：{tool_call.get('args', 'N/A')}")
                logger.debug(f"  - 调用 ID: {tool_call.get('id', 'N/A')}")
        else:
            logger.info("LLM 未调用工具，直接返回结果")

        if hasattr(response, "response_metadata"):
            logger.debug(f"响应元数据：{response.response_metadata}")

        logger.debug("=" * 80)

        return {
            "messages": [response],
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    except Exception as e:
        logger.error(f"LLM 调用失败：{type(e).__name__}: {str(e)}")
        logger.exception("详细错误信息：")
        raise
