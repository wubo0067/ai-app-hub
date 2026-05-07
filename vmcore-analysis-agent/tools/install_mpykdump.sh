#!/bin/bash

# 判断 crash 命令是否存在
if ! command -v crash &> /dev/null; then
    echo "错误: 未找到 crash 命令。请先安装 crash 工具。"
    exit 1
fi

# 获取 crash 版本信息
CRASH_VERSION_OUTPUT=$(crash -v 2>&1)
# 提取版本号（例如：从 "crash 9.0.0-4.el9" 提取 "9.0" 或 "8.0" 等主要版本）
CRASH_VERSION=$(echo "$CRASH_VERSION_OUTPUT" | grep -m 1 -oE 'crash [0-9]+\.[0-9]+' | grep -oE '[0-9]+\.[0-9]+')

# 检查版本并拷贝对应的 .so 文件
if [[ "$CRASH_VERSION" == 9.0* ]]; then
    SO_FILE="third_party/mpykdump/mpykdump-3.10.1-crash9.so"
elif [[ "$CRASH_VERSION" == 8.0* ]]; then
    SO_FILE="third_party/mpykdump/mpykdump-3.10.1-crash8.so"
else
    echo "警告: 当前 crash 版本 ($CRASH_VERSION) 无法安装自带的 mpykdump 扩展，目前自带仅支持 8.0 和 9.0 版本。"
    echo "请前往 https://sourceforge.net/projects/pykdump/files/mpykdump-x86_64/ 下载对应版本的扩展并手动加载。"
    exit 1
fi

# 确保脚本处于项目根目录或 tools 目录下执行，以下查找 .so 的绝对路径
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
PROJECT_ROOT=$(dirname "$SCRIPT_DIR")
SO_PATH="$PROJECT_ROOT/../$SO_FILE"

if [ ! -f "$SO_PATH" ]; then
    echo "错误: 在路径 $SO_PATH 下未找到对应的 .so 文件。"
    # 尝试在同级或预设路径寻找
    exit 1
fi

TARGET_SO_NAME=$(basename "$SO_FILE")

# 拷贝到 /usr/local/lib 路径下
echo "正在拷贝 $SO_PATH 到 /usr/local/lib/$TARGET_SO_NAME ..."
sudo cp "$SO_PATH" "/usr/local/lib/$TARGET_SO_NAME"

if [ $? -eq 0 ]; then
    echo "安装成功！"
    echo "请在运行 crash 后输入指令: extend /usr/local/lib/$TARGET_SO_NAME 即可加载扩展。"
else
    echo "安装失败，请检查是否具备 sudo 权限或目标路径是否可写。"
    exit 1
fi
