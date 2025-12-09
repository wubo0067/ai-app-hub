import logging
import os
import queue
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener


# 全局日志记录器和队列监听器
logger = None
_queue_listener = None


def setup_logger(config):
    """根据配置初始化日志模块"""
    global logger, _queue_listener

    log_config = config.get('logging', {}) if config else {}
    level = log_config.get('level', 'INFO')
    log_file = log_config.get('file', './logs/import.log')
    max_size = log_config.get('max_size', '10MB')
    backup_count = log_config.get('backup_count', 5)
    handlers_config = log_config.get('handlers', {})

    # 创建日志目录
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # 解析 max_size（如 "10MB","1024KB","100"）
    size = str(max_size).strip().upper()
    try:
        if size.endswith('MB'):
            max_bytes = int(size[:-2]) * 1024 * 1024
        elif size.endswith('KB'):
            max_bytes = int(size[:-2]) * 1024
        elif size.endswith('GB'):
            max_bytes = int(size[:-2]) * 1024 * 1024 * 1024
        else:
            max_bytes = int(size)
    except Exception:
        max_bytes = 10 * 1024 * 1024  # 默认 10MB

    # 创建或获取日志记录器
    logger = logging.getLogger('import_solutions')
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    # 清除已有处理器，避免重复记录
    if logger.handlers:
        logger.handlers.clear()

    # 日志格式
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 检查是否使用 QueueHandler
    use_queue_handler = (
        handlers_config.get('file', {}).get('class') == 'logging.handlers.QueueHandler'
    )

    if use_queue_handler:
        # 使用 QueueHandler 异步处理日志
        log_queue = queue.Queue()
        queue_handler = QueueHandler(log_queue)
        queue_handler.setLevel(logging.NOTSET)
        logger.addHandler(queue_handler)

        # 创建实际的文件和控制台处理器
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        console_handler = logging.StreamHandler()

        file_handler.setLevel(logging.NOTSET)
        console_handler.setLevel(logging.NOTSET)
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        # 启动 QueueListener（在后台线程中处理日志）
        if _queue_listener is not None:
            _queue_listener.stop()
        _queue_listener = QueueListener(log_queue, file_handler, console_handler)
        _queue_listener.start()
    else:
        # 传统方式：文件处理器（轮转日志）
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )

        # 控制台处理器
        console_handler = logging.StreamHandler()

        # 确保 handler 不再额外过滤（由 logger.level 控制）
        file_handler.setLevel(logging.NOTSET)
        console_handler.setLevel(logging.NOTSET)

        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        # 添加处理器
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    # 防止向上级 logger 传播，避免重复输出
    logger.propagate = False

    return logger


def shutdown_logger():
    """关闭日志系统"""
    global _queue_listener
    if _queue_listener is not None:
        _queue_listener.stop()
        _queue_listener = None
