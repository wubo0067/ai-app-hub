# client.py
"""
Vmcore Analysis Agent 客户端
支持同步请求和流式请求两种模式
"""
import argparse
import httpx
import json
import sys
import os
import re
from typing import Optional
from pathlib import Path
from datetime import datetime


def analyze_vmcore(
    base_url: str,
    vmcore_path: str,
    vmlinux_path: str,
    vmcore_dmesg_path: str,
    debug_symbol_paths: Optional[list[str]] = None,
    timeout: float = 600.0,
) -> dict:
    """
    同步模式分析 vmcore

    Args:
        base_url: API 服务地址
        vmcore_path: vmcore 文件路径
        vmlinux_path: vmlinux 调试符号路径
        vmcore_dmesg_path: vmcore-dmesg.txt 文件路径
        debug_symbol_paths: 额外的调试符号路径列表
        timeout: 请求超时时间（秒）

    Returns:
        分析结果字典
    """
    url = f"{base_url}/analyze"
    payload = {
        "vmcore_path": vmcore_path,
        "vmlinux_path": vmlinux_path,
        "vmcore_dmesg_path": vmcore_dmesg_path,
        "debug_symbol_paths": debug_symbol_paths or [],
    }

    print(f"🚀 发送分析请求到 {url}")
    print(f"📁 vmcore_path: {vmcore_path}")
    print(f"📁 vmlinux_path: {vmlinux_path}")
    print(f"📁 vmcore_dmesg_path: {vmcore_dmesg_path}")
    print(f"📁 debug_symbol_paths: {debug_symbol_paths}")
    print("-" * 60)

    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


def analyze_vmcore_stream(
    base_url: str,
    vmcore_path: str,
    vmlinux_path: str,
    vmcore_dmesg_path: str,
    debug_symbol_paths: Optional[list[str]] = None,
    timeout: float = 600.0,
) -> dict:
    """
    流式模式分析 vmcore，实时打印进度

    Args:
        base_url: API 服务地址
        vmcore_path: vmcore 文件路径
        vmlinux_path: vmlinux 调试符号路径
        vmcore_dmesg_path: vmcore-dmesg.txt 文件路径
        debug_symbol_paths: 额外的调试符号路径列表
        timeout: 请求超时时间（秒）

    Returns:
        最终分析结果字典
    """
    url = f"{base_url}/analyze/stream"
    payload = {
        "vmcore_path": vmcore_path,
        "vmlinux_path": vmlinux_path,
        "vmcore_dmesg_path": vmcore_dmesg_path,
        "debug_symbol_paths": debug_symbol_paths or [],
    }

    print(f"🚀 发送流式分析请求到 {url}")
    print(f"📁 vmcore_path: {vmcore_path}")
    print(f"📁 vmlinux_path: {vmlinux_path}")
    print(f"📁 vmcore_dmesg_path: {vmcore_dmesg_path}")
    print(f"📁 debug_symbol_paths: {debug_symbol_paths}")
    print("-" * 60)

    final_result = None

    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]  # 去掉 "data: " 前缀
                    try:
                        data = json.loads(data_str)
                        event = data.get("event")

                        if event == "start":
                            print(f"✅ 任务开始：task_id={data.get('task_id')}")
                        elif event == "node_start":
                            print(f"🚦 节点启动：{data.get('node')}")
                        elif event == "node_complete":
                            print(
                                f"📍 节点完成：{data.get('node')} | Token: {data.get('token_usage', 0)}"
                            )
                        elif event == "tool_start":
                            print(f"🔧 工具执行中：{data.get('tool')} ...")
                        elif event == "tool_end":
                            print(f"✓ 工具完成：{data.get('tool')}")
                        elif event == "complete":
                            print("-" * 60)
                            print("🎉 分析完成！")
                            final_result = {
                                "success": True,
                                "agent_answer": data.get("agent_answer", ""),
                                "token_usage": data.get("token_usage", 0),
                                "error": data.get("error"),
                            }
                        elif event == "error":
                            print(f"❌ 错误：{data.get('error')}")
                            final_result = {
                                "success": False,
                                "agent_answer": "",
                                "token_usage": 0,
                                "error": data.get("error"),
                            }
                    except json.JSONDecodeError:
                        continue

    return final_result or {"success": False, "error": "No response received"}


def health_check(base_url: str) -> dict:
    """检查服务健康状态"""
    url = f"{base_url}/health"
    with httpx.Client(timeout=10.0) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def save_markdown_report(
    agent_answer: str, vmcore_path: str, output_dir: str = "."
) -> str:
    """
    保存 markdown 分析报告到文件。

    Args:
        agent_answer: markdown 格式的分析报告
        vmcore_path: vmcore 文件路径，用于提取命名信息
        output_dir: 输出目录

    Returns:
        str: 保存的文件路径
    """
    # 从 vmcore_path 中提取目录名作为文件名
    # 例如：/var/crash/127.0.0.1-2026-01-30-22:51:43/vmcore -> 127.0.0.1-2026-01-30-22:51:43
    vmcore_dir = Path(vmcore_path).parent.name

    # 清理文件名中的非法字符（主要是冒号）
    safe_filename = re.sub(r'[:<>"|?*]', "-", vmcore_dir)

    # 如果提取失败，使用时间戳
    if not safe_filename or safe_filename == ".":
        safe_filename = datetime.now().strftime("%Y%m%d-%H%M%S")

    # 构造文件名
    filename = f"{safe_filename}.md"
    filepath = Path(output_dir) / filename

    # 确保输出目录存在
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # 写入文件
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(agent_answer)

    return str(filepath)


def main():
    parser = argparse.ArgumentParser(description="Vmcore Analysis Agent 客户端")
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="API 服务地址 (默认：http://localhost:8000)",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="使用流式模式",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="仅检查服务健康状态",
    )
    parser.add_argument(
        "--vmcore-path",
        default="/var/crash/127.0.0.1-2026-01-30-22:51:43/vmcore",
        help="vmcore 文件路径",
    )
    parser.add_argument(
        "--vmlinux-path",
        default="/usr/lib/debug/lib/modules/5.14.0-362.8.1.el9_3.x86_64/vmlinux",
        help="vmlinux 调试符号路径",
    )
    parser.add_argument(
        "--vmcore-dmesg-path",
        default="/var/crash/127.0.0.1-2026-01-30-22:51:43/vmcore-dmesg.txt",
        help="vmcore-dmesg.txt 文件路径",
    )
    parser.add_argument(
        "--debug-symbols",
        nargs="*",
        default=[
            "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/soft_lockup/soft_lockup_module.ko",
            "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko",
        ],
        help="额外的调试符号路径列表",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="请求超时时间（秒）(默认：600)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="报告输出目录 (默认：当前目录)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="不保存 markdown 报告文件",
    )

    args = parser.parse_args()

    try:
        if args.health:
            print(f"🔍 检查服务健康状态：{args.url}")
            result = health_check(args.url)
            print(f"状态：{json.dumps(result, indent=2, ensure_ascii=False)}")
            return

        if args.stream:
            result = analyze_vmcore_stream(
                base_url=args.url,
                vmcore_path=args.vmcore_path,
                vmlinux_path=args.vmlinux_path,
                vmcore_dmesg_path=args.vmcore_dmesg_path,
                debug_symbol_paths=args.debug_symbols,
                timeout=args.timeout,
            )
        else:
            result = analyze_vmcore(
                base_url=args.url,
                vmcore_path=args.vmcore_path,
                vmlinux_path=args.vmlinux_path,
                vmcore_dmesg_path=args.vmcore_dmesg_path,
                debug_symbol_paths=args.debug_symbols,
                timeout=args.timeout,
            )

        print("\n" + "=" * 60)
        print("📊 分析结果：")
        print("=" * 60)

        if result.get("success"):
            print(f"✅ 成功")
            if result.get("task_id"):
                print(f"📋 Task ID: {result.get('task_id')}")
            print(f"📈 Token 使用量：{result.get('token_usage', 0)}")

            agent_answer = result.get("agent_answer", "")
            if agent_answer:
                print(f"\n📝 Agent 回答:\n{agent_answer}")

                # 保存 markdown 文件
                if not args.no_save:
                    try:
                        filepath = save_markdown_report(
                            agent_answer, args.vmcore_path, args.output_dir
                        )
                        print(f"\n💾 报告已保存到：{filepath}")
                    except Exception as save_err:
                        print(f"\n⚠️ 保存报告失败：{save_err}")
            else:
                print(f"\n⚠️ Agent 回答为空")
        else:
            print(f"❌ 失败")
            print(f"错误：{result.get('error', 'Unknown error')}")

    except httpx.ConnectError:
        print(f"❌ 无法连接到服务：{args.url}")
        print("请确保服务已启动")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"❌ HTTP 错误：{e.response.status_code}")
        print(f"响应：{e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 错误：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
