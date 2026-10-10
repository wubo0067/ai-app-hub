# c,ient.py
"""
Vmcore Analysis Agent 客户端库
提供同步请求、流式请求、健康检查和报告保存等可复用接口
"""
import httpx
import json
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
                                "audit_report": data.get("audit_report", ""),
                                "token_usage": data.get("token_usage", 0),
                                "error": data.get("error"),
                            }
                        elif event == "error":
                            print(f"❌ 错误：{data.get('error')}")
                            final_result = {
                                "success": False,
                                "agent_answer": "",
                                "audit_report": "",
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


def _report_basename(vmcore_path: str) -> str:
    """从 vmcore 路径推导报告文件名前缀（清理非法字符）。"""
    # 例如：/var/crash/127.0.0.1-2026-01-30-22:51:43/vmcore -> 127.0.0.1-2026-01-30-22:51:43
    vmcore_dir = Path(vmcore_path).parent.name
    safe_filename = re.sub(r'[:<>"|?*]', "-", vmcore_dir)
    if not safe_filename or safe_filename == ".":
        safe_filename = datetime.now().strftime("%Y%m%d-%H%M%S")
    return safe_filename


def save_markdown_report(
    agent_answer: str, vmcore_path: str, output_dir: str = "./reports"
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
    # 构造文件名，追加时间戳避免覆盖
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{_report_basename(vmcore_path)}-{timestamp}.md"
    filepath = Path(output_dir) / filename

    # 确保输出目录存在
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # 写入文件
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(agent_answer)

    return str(filepath)


def save_gate_audit_report(
    audit_report: str,
    vmcore_path: str,
    report_path: str,
    output_dir: str = "./reports",
) -> Optional[str]:
    """
    保存独立的 Gate 审计记录。

    审计文件与主报告同名但追加 `.audit` 后缀，便于对照查找。

    Args:
        audit_report: markdown 格式的审计记录；为空表示无 gate 记录
        vmcore_path: vmcore 文件路径，用于提取命名信息
        report_path: 主报告文件路径，审计文件取其文件名（去掉时间戳后复用）
        output_dir: 输出目录

    Returns:
        Optional[str]: 保存的文件路径；无审计内容时返回 None
    """
    if not audit_report:
        return None

    base = Path(report_path).stem if report_path else _report_basename(vmcore_path)
    filepath = Path(output_dir) / f"{base}.audit.md"
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(audit_report)

    return str(filepath)
