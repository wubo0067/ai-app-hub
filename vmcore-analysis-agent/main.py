import asyncio
import yaml
from src.utils.logging import logger
from src.utils.config import config_manager
from src.utils.model import llm


async def main():
    logger.info("Hello from vmcore-analysis-agent!")
    logger.info(
        f"config: \n{yaml.dump(config_manager.get_all(), allow_unicode=True, sort_keys=False)}"
    )
    logger.info(f"LLM Model: {llm.model_name}")


if __name__ == "__main__":
    asyncio.run(main())
