# main.py
"""
Vmcore Analysis Agent 客户端入口
"""
import argparse
import json
import sys
import httpx
from client import (
    analyze_vmcore,
    analyze_vmcore_stream,
    health_check,
    save_markdown_report,
)


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
        default="../reports",
        help="报告输出目录 (默认：./reports)",
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
            print("✅ 成功")
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
                print("\n⚠️ Agent 回答为空")
        else:
            print("❌ 失败")
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
