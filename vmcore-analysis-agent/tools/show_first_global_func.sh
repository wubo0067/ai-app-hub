#!/bin/bash

KO="$1"

if [ -z "$KO" ]; then
    echo "用法: $0 <path/to/module.ko>"
    exit 1
fi

if [ ! -f "$KO" ]; then
    echo "错误: 文件不存在: $KO"
    exit 1
fi

FUNC=$(readelf -s "$KO" | awk '
    $4=="FUNC" && $5=="GLOBAL" && $6=="DEFAULT" && $8 !~ /\[/ {
        print $8
        exit
    }
')

if [ -z "$FUNC" ]; then
    echo "未找到符合条件的函数符号（FUNC GLOBAL DEFAULT 且未被截断）"
    exit 1
fi

echo "找到的第一个函数符号: " "$FUNC"

gdb -q "$KO" -ex "list $FUNC" -ex "quit"
