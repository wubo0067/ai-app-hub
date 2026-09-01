#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图片 -> Markdown 提取（印刷体、公式、图形标签、彩色手写批注、思维导图、表格）。

流程：逐张读取图片 -> 调用在线视觉大模型（OpenAI 兼容接口）按提示词提取
-> 按页汇总导出 Markdown。
默认处理 output/ 下的 chk_p4.png、chk_p5.png、chk_p11.png 三张图片，
结果写入 output/notes.md。提取结果按（图片内容 + 提示词）哈希缓存，
重复运行直接复用缓存，避免重复调用模型。
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Iterable

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

BASE_DIR = Path(__file__).resolve().parent

# ---- 待处理图片与输出路径 ------------------------------------------------
IMAGE_DIR = BASE_DIR / "output"                    # 图片所在目录
IMAGE_FILES = ["chk_p4.png", "chk_p5.png", "chk_p11.png"]  # 待处理图片
OUTPUT_PATH = BASE_DIR / "output" / "notes.md"     # 导出 Markdown
CACHE_DIR = BASE_DIR / "cache"                     # 提取结果缓存目录

# ---- 在线视觉模型配置（阿里云百炼 DashScope，OpenAI 兼容接口）------------
BASE_URL = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen3.6-flash"
API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")  # 从环境变量读取，避免硬编码密钥

LLM_TIMEOUT = 300.0      # 单次请求超时（秒），在线模型一般几十秒内返回
LLM_RETRIES = 2          # 调用失败重试次数
MAX_TOKENS = 8192        # 单次生成最大 token 数

# ---- 图片内容提取提示词 ---------------------------------------------------
PROMPT = """请仔细阅读并提取这张图片中的所有内容，包括所有印刷体文本、数学公式、电路图（如有，提取标签即可）以及手写编辑内容。为了确保精确，请遵循以下结构化指令：1. 整体排版顺序：请严格按照图片从上到下、从左到右的阅读逻辑进行文本输出，并使用 Markdown 格式进行清晰的层次划分。2. 印刷体文本提取：提取所有标准的印刷体文本，包括标题、正文、列表、题目干以及选项。请尽量保持原有的标点符号和大小写。3. 公式与符号处理：所有的数学公式、物理符号、科学常数（如 $\\text{U, I, R}$, $\\text{L}_{\\text{eff}}$, 欧姆定律等）必须使用原生的 LaTeX 格式（例如：$I = \\frac{U}{R}$, $P_{\\text{total}} = \\int(V \\cdot I)dt$, \\Sigma\\text{V}-\\text{Idt}）进行输出。4. 颜色敏感的手写内容：这是关键部分。请特别留意图片中的彩色笔（如红笔、紫笔、绿笔）所做的手写编辑、推导和符号。遇见的彩色手写内容，请尽最大努力转录其字符（符号使用 LaTeX），并必须用中括号注明笔刷颜色，例如：[红笔手写：$P=UI=I^2R=\\frac{U^2}{R}$]。请注意手写符号与印刷体公式之间的指向关系，例如如果红笔画了圈并写了推导，请在相应的印刷公式下方进行输出。如果是题目旁边的选项手写批改（如 $AC$），也请标注出颜色。5. 图形与标签：如果图片包含电路图、流程图或其他图形：不需要描述图形的具体样子。但必须提取出图中所有的文本标签（例如 $A_1, A_2, V, P, S$）和图形自带的手写标记。如果手写笔记引用了图形中的某个元件，请确保提及。6. 处理复杂和模糊：如果部分手写内容过于模糊，可以注明[此处手写内容模糊]。请开始逐段进行全面的读取和提取。"""


def _make_client() -> OpenAI:
    """构造 OpenAI 客户端，访问阿里云百炼在线模型。

    使用 SDK 默认 http 客户端（trust_env=True），自动读取系统代理
    （本机 127.0.0.1:7892）访问外网 DashScope 接口。
    """
    return OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=LLM_TIMEOUT)


def _image_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _fix_math(text: str) -> str:
    """修复模型输出中导致 KaTeX 报错的 $ 写法。

    典型错误：模型输出 $$...$$ 后紧跟正文时，渲染器按单个 $ 配对定界符，
    数学模式内部混入多余的 $，报
    "Can't use function '$' in math mode"。
    这里统一把成对的 $$ 折叠为单个 $（行内公式），KaTeX 可正常渲染。
    """
    return re.sub(r"\$\$", "$", text)


def _chat(client: OpenAI, messages: Iterable[ChatCompletionMessageParam]) -> str:
    last_error: Exception | None = None
    for attempt in range(LLM_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=list(messages),
                max_tokens=MAX_TOKENS,
                temperature=0.0,
                timeout=LLM_TIMEOUT,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < LLM_RETRIES:
                wait = 2 * (attempt + 1)
                print(f"    ! 调用失败，{wait}s 后重试：{exc}")
                time.sleep(wait)
    raise RuntimeError(f"LLM 调用最终失败：{last_error}")


def _cache_key(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    h.update(PROMPT.encode("utf-8"))
    return h.hexdigest()


def extract_image(client: OpenAI, path: Path) -> str:
    """提取单张图片：命中缓存直接返回，否则调用模型并写入缓存。

    空缓存（如上次运行被中断产生的 0 字节文件）视为未命中，重新提取；
    模型返回空结果时不写缓存，避免污染。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{_cache_key(path)}.md"
    if cache_file.exists():
        cached = cache_file.read_text(encoding="utf-8").strip()
        if cached:
            print(f"  [缓存] {path.name}")
            return _fix_math(cached)
        cache_file.unlink()  # 删除空缓存，重新提取

    print(f"  [提取] {path.name} ...")
    messages: list[ChatCompletionMessageParam] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{_image_b64(path)}"},
                },
            ],
        }
    ]
    text = _chat(client, messages)
    if text:
        cache_file.write_text(text, encoding="utf-8")
    else:
        print(f"  ! {path.name}：模型返回内容为空，未写入缓存")
    return _fix_math(text)


def process_images(client: OpenAI) -> str:
    """处理所有待提取图片，返回汇总的 Markdown 文档。"""
    sections: list[str] = []
    for name in IMAGE_FILES:
        img = IMAGE_DIR / name
        if not img.exists():
            print(f"  ! 跳过（不存在）：{img}")
            continue
        body = extract_image(client, img)
        sections.append(f"<!-- {name} -->\n\n## {name}\n\n{body}")
    return "\n\n---\n\n".join(sections)


def main() -> None:
    if not API_KEY:
        print("错误：未设置 DASHSCOPE_API_KEY 环境变量。")
        print("请先运行：")
        print('  $env:DASHSCOPE_API_KEY="你的阿里云百炼 API Key"')
        print("（阿里云百炼控制台：https://bailian.console.aliyun.com 获取 API Key）")
        raise SystemExit(1)

    client = _make_client()
    print(f"模型：{MODEL_NAME}（{BASE_URL}）")
    print(f"待处理图片：{IMAGE_FILES}")
    doc = process_images(client)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(doc, encoding="utf-8")
    print(f"已导出：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()

