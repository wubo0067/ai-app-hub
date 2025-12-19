import logging
from typing import Optional


# 全局 logger 对象
logger = logging.getLogger("agent")


def setup_logger(
    level: str = "INFO",
    log_file: Optional[
        str
    ] = "agent.log",  # log_file 类型是 str 或 None，默认值是 "agent.log"
    fmt: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
):
    """
    动态配置日志系统。
    main.py 或外部模块可以调用这个函数来设置日志级别、格式、输出位置等。
    """

    # 将字符串级别转换为 logging 的常量
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)

    # 避免重复添加 handler（否则会重复输出）
    if logger.handlers:
        logger.handlers.clear()

    formatter = logging.Formatter(fmt)

    # 控制台输出
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件输出（可选）
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
