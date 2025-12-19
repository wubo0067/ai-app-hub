from langchain_core.callbacks import BaseCallbackHandler
from log import logger


class LoggingCallbackHandler(BaseCallbackHandler):
    def on_chain_start(self, serialized, inputs, **kwargs):
        logger.info(f"Starting graph execution with inputs: {inputs}")

    def on_chain_end(self, outputs, **kwargs):
        logger.info(f"Graph execution ended with outputs: {outputs}")

    def on_node_start(self, node_name: str, inputs: dict, **kwargs):
        logger.info(f"Starting node '{node_name}' with inputs: {inputs}")

    def on_node_end(self, node_name: str, outputs: dict, **kwargs):
        logger.info(f"Node '{node_name}' ended with outputs: {outputs}")


logging_callback_handler = LoggingCallbackHandler()
