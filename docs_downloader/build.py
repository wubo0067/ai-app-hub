#!/usr/bin/env python3
"""
打包脚本：使用 PyInstaller 将 web_downloader_pro.py 打包成独立的 exe。

用法：
    uv run python build.py

产物在 dist/ 目录下：
    web-downloader.exe  （单文件，双击或命令行运行）

注意：
    - config.ini 不会被打包进 exe，需要和 exe 放在同一目录下
    - 命令行用法：web-downloader.exe [配置文件路径]
"""

import subprocess
import sys

PYINSTALLER_ARGS = [
    "pyinstaller",
    "--onefile",                 # 单文件 exe
    "--console",                 # 控制台程序（显示日志）
    "--name", "web-downloader",  # exe 文件名
    "--distpath", "./dist",      # 输出目录
    "--workpath", "./build",     # 临时构建目录
    "--clean",                   # 清理缓存
    "web_downloader_pro.py",
]


def main():
    print("=" * 60)
    print("开始打包 web-downloader...")
    print("=" * 60)

    result = subprocess.run(PYINSTALLER_ARGS)

    if result.returncode == 0:
        print("\n✅ 打包成功！")
        print(f"   exe 位置：dist\\web-downloader.exe")
        print(f"\n使用方式：")
        print(f"   1. 将 config.ini 放到 exe 同目录下")
        print(f"   2. 双击 web-downloader.exe")
        print(f"      或命令行：web-downloader.exe 其他配置.ini")
    else:
        print(f"\n❌ 打包失败，错误码：{result.returncode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
