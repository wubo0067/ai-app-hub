from langchain_core.callbacks import BaseCallbackHandler
from typing import Any, Dict, List, Optional
import asyncio
from src.utils.logging import logger


class GraphLoggingCallback(BaseCallbackHandler):
    """
    自定义回调处理器，用于记录 LangGraph 执行过程中的节点转换和状态变化。
    """

    def __init__(self):
        super().__init__()
        self.current_node = None

    def on_chain_start(
        self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs: Any
    ) -> None:
        """当整个图开始执行时调用"""
        # logger.info("=" * 80)
        # logger.info("🚀 Graph execution started")
        # logger.info(f"Initial inputs: {self._format_inputs(inputs)}")
        # logger.info("=" * 80)
        pass

    def on_chain_end(self, outputs: Dict[str, Any], **kwargs: Any) -> None:
        """当整个图执行结束时调用"""
        # logger.info("=" * 80)
        # logger.info("✅ Graph execution completed")
        # logger.info(f"Final outputs: {self._format_outputs(outputs)}")
        # logger.info("=" * 80)
        pass

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        """当图执行出错时调用"""
        if isinstance(error, asyncio.CancelledError):
            logger.info("⚠️ Graph execution was cancelled.")
            return

        logger.error("=" * 80)
        logger.error(f"❌ Graph execution failed with error: {error}")
        logger.error("=" * 80)

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """当工具开始执行时调用"""
        tool_name = serialized.get("name", "unknown_tool")
        logger.info(f"  🔧 Tool '{tool_name}' started")
        logger.debug(f"    Input: {input_str[:200]}...")  # 只显示前 200 字符

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        """当工具执行结束时调用"""
        # logger.info(f"  ✓ Tool completed")
        # logger.debug(f"    Output: {output[:200]}...")  # 只显示前 200 字符
        pass

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """当工具执行出错时调用"""
        logger.error(f"  ✗ Tool failed: {error}")

    def on_llm_start(
        self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any
    ) -> None:
        """当 LLM 开始调用时"""
        # logger.info(f"  🤖 LLM invocation started")
        # logger.debug(f"    Prompts count: {len(prompts)}")
        pass

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """当 LLM 调用结束时"""
        logger.info(f"  ✓ LLM invocation completed")

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """当 LLM 调用出错时"""
        logger.error(f"  ✗ LLM invocation failed: {error}")

    def on_agent_action(self, action: Any, **kwargs: Any) -> None:
        """当 Agent 采取行动时"""
        logger.info(f"  🎯 Agent action: {action}")

    def on_agent_finish(self, finish: Any, **kwargs: Any) -> None:
        """当 Agent 完成时"""
        logger.info(f"  🏁 Agent finished: {finish}")

    def _format_inputs(self, inputs: Dict[str, Any]) -> Dict[str, Any] | str:
        """格式化输入以便于日志记录"""
        if not isinstance(inputs, dict):
            return str(inputs)

        formatted = {}
        for k, v in inputs.items():
            if k in ["vmcore_path", "vmlinux_path", "step_count"]:
                formatted[k] = v
            elif k == "messages":
                formatted[k] = f"<{len(v)} messages>" if isinstance(v, list) else v
            else:
                formatted[k] = str(v)[:100] + "..." if len(str(v)) > 100 else v
        return formatted

    def _format_outputs(self, outputs: Dict[str, Any]) -> Dict[str, Any] | str:
        """格式化输出以便于日志记录"""
        if not isinstance(outputs, dict):
            return str(outputs)

        formatted = {}
        for k, v in outputs.items():
            if k in ["step_count", "agent_answer", "error"]:
                formatted[k] = v
            elif k == "messages":
                formatted[k] = f"<{len(v)} messages>" if isinstance(v, list) else v
            else:
                formatted[k] = str(v)[:100] + "..." if len(str(v)) > 100 else v
        return formatted


# 全局回调处理器实例
graph_logging_callback = GraphLoggingCallback()
