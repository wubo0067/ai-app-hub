#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# logging.py - 日志配置和管理模块
# Author: CalmWU
# Created: 2026-01-06

import logging
import os


# Define a custom path filter to shorten log pathnames
def custom_path_filter(path):
    # Define the project root name
    project_root = "vmcore-analysis-agent"

    # Find the index of the project root in the path
    idx = path.find(project_root)
    if idx != -1:
        # Extract the portion of the path after the project root
        path = path[
            idx + len(project_root) + 1 :
        ]  # +1 to include the separator after project root
    else:
        # If project root is not found, return the basename of the file
        path = os.path.basename(path)
    return path


# Define a custom LogRecord class to modify the pathname
class CustomLogRecord(logging.LogRecord):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pathname = custom_path_filter(self.pathname)


# Function to set up the logger
def setup_logger(log_filename="va-agent.log", log_dir="logs"):
    # Ensure the logging directory exists
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Define the log file path
    log_filepath = os.path.join(log_dir, log_filename)

    # Get or create logger
    logger_instance = logging.getLogger("vmcore_analysis_agent")

    # Avoid adding handlers multiple times if logger already exists
    if not logger_instance.handlers:
        # Define the logging configuration
        logging.setLogRecordFactory(CustomLogRecord)  # Only set once globally
        handler = logging.FileHandler(log_filepath)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(module)s] [%(pathname)s:%(lineno)d]: %(message)s"
        )
        handler.setFormatter(formatter)
        handler.setLevel(logging.DEBUG)  # 显式设置文件处理器的级别
        logger_instance.addHandler(handler)

        # Also add console handler for debugging
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.DEBUG)  # 显式设置控制台处理器的级别
        logger_instance.addHandler(console_handler)

    # 确保无论是否已存在 Handler（例如由于多次调用或环境预初始化），Logger 的级别都设置为 DEBUG
    logger_instance.setLevel(logging.DEBUG)

    return logger_instance


# Global logger object
logger = setup_logger()
