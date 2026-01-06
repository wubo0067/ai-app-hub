import json
import os
from typing import Any, Dict, Optional
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class ConfigFileHandler(FileSystemEventHandler):
    """
    配置文件变化处理器
    """

    def __init__(self, config_manager):
        super().__init__()
        self.config_manager = config_manager

    def on_modified(self, event):
        if event.is_directory:
            return
        if event.src_path.endswith(self.config_manager._config_file_path):
            self.config_manager._handle_file_change()


class ConfigManager:
    """
    单例模式的配置管理类
    """

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._config: Dict[str, Any] = {}
        self._config_file_path: Optional[str] = "config/config.yml"  # 默认配置文件路径
        self._last_modified_time: float = 0  # 记录文件最后修改时间
        self._observer = Observer()  # 文件系统观察器
        self._event_handler = None
        self._initialized = True

        # 初始化时加载默认配置文件并开始监控
        self._load_config_if_needed()
        self._start_watching()

    def _start_watching(self):
        """
        开始监控配置文件变化
        """
        if self._config_file_path and os.path.exists(self._config_file_path):
            directory = os.path.dirname(os.path.abspath(self._config_file_path))
            self._event_handler = ConfigFileHandler(self)
            self._observer.schedule(self._event_handler, directory, recursive=False)
            self._observer.start()

    def _stop_watching(self):
        """
        停止监控配置文件变化
        """
        if self._observer.is_alive():
            self._observer.stop()
            self._observer.join()

    def _handle_file_change(self):
        """
        处理文件变化事件
        """
        # 添加一个小延迟，避免在文件写入过程中读取
        time.sleep(0.1)
        current_modified_time = os.path.getmtime(self._config_file_path)
        if current_modified_time != self._last_modified_time:
            try:
                # 尝试加载新的配置
                with open(self._config_file_path, "r", encoding="utf-8") as f:
                    # 由于 config.yml 是 YAML 格式，需要使用 yaml 解析器
                    import yaml

                    new_config = yaml.safe_load(f)
                    if new_config is not None:
                        self._config = new_config
                        self._last_modified_time = current_modified_time
            except Exception as e:
                print(f"Error loading config file: {e}")

    def _load_config_if_needed(self) -> None:
        """
        检查配置文件是否需要重新加载
        """
        if self._config_file_path and os.path.exists(self._config_file_path):
            current_modified_time = os.path.getmtime(self._config_file_path)
            if current_modified_time != self._last_modified_time:
                with open(self._config_file_path, "r", encoding="utf-8") as f:
                    import yaml

                    self._config = yaml.safe_load(f) or {}
                self._last_modified_time = current_modified_time

    def load_from_file(self, file_path: str) -> None:
        """
        从文件加载配置

        Args:
            file_path: 配置文件路径
        """
        # 停止之前的监控
        self._stop_watching()

        self._config_file_path = file_path
        self._load_config_if_needed()

        # 重新开始监控新文件
        self._start_watching()

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值，自动检查文件是否需要重新加载

        Args:
            key: 配置键
            default: 默认值

        Returns:
            配置值
        """
        # 不需要手动检查文件，因为文件变化会自动更新
        return self._config.get(key, default)

    def get_all(self) -> Dict[str, Any]:
        """
        获取所有配置，自动检查文件是否需要重新加载

        Returns:
            所有配置的副本
        """
        # 不需要手动检查文件，因为文件变化会自动更新
        return self._config.copy()

    def __del__(self):
        """
        析构函数，确保在对象销毁时停止监控
        """
        try:
            self._stop_watching()
        except:
            pass  # 忽略异常，避免在析构时出现问题


# 全局配置管理实例
config_manager = ConfigManager()
