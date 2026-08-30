#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# main.py - check-pdf-annotation
# 教材 PDF → Markdown 提取流水线：
#   pymupdf4llm 提取正文 + PyMuPDF 渲染高清 PNG
#   → ollama 多模态识别手写笔记 → ollama LLM 整理 → 按页合并
# Author: CalmWU
# Created: 2026-08-30

import argparse
import base64
import re
import time
from pathlib import Path
from typing import cast

import httpx2
import pymupdf
import pymupdf4llm
from openai import OpenAI

# ---------------- 配置 ----------------
PDF_PATH = r"H:\wechat_files\xwechat_files\calm-wu_9d75\msg\file\2026-08\9S合并PDF.pdf"
OUTPUT_PATH = "output/notes.md"
CACHE_DIR = "cache"

BASE_URL = "http://localhost:11636/v1"
MODEL_NAME = "qwen3.8:27b-mtp-q4_K_M"

RENDER_ZOOM = 2          # 渲染倍数，2x≈144DPI
MAX_LONG_EDGE = 2000     # 图片长边像素上限，超限自动降倍数
LLM_TIMEOUT = 900        # 单次请求超时（秒），需覆盖模型冷启动加载
LLM_RETRIES = 1          # 失败重试次数

NO_NOTE_MARK = "NO_HANDWRITING"

PROMPT_VISION = f"""\
这是一页教材的渲染图。图中印刷体是教材原文，手写笔迹是老师批注。
请只转录手写内容，要求：
1. 不要复述任何印刷体文字；
2. 按手写块在页面上出现的顺序（从上到下）输出，每块前用【位置】标注大致区域\
（如 左上/右侧/页脚/行间）；
3. 数学公式用 LaTeX（$...$ 或 $$...$$）表示；
4. 无法辨认的字用 [?] 标记，严禁猜测编造。
如果整页没有任何手写笔迹，只输出：{NO_NOTE_MARK}
"""

# 说明：为提速已去掉"LLM 整理"步骤（方案1），视觉识别结果直接进入合并。

# ---------------- ① 教材正文 ----------------
def extract_text_pages(doc: pymupdf.Document) -> list[str]:
    """逐页提取教材 Markdown 正文。"""
    chunks = cast(list[dict], pymupdf4llm.to_markdown(
        doc, page_chunks=True, write_images=False))
    return [c["text"] for c in chunks]


# ---------------- ② 渲染高清 PNG ----------------
def render_page_b64(page: pymupdf.Page) -> str:
    """渲染页面为 PNG 并转 base64，长边超过 MAX_LONG_EDGE 时自动降倍数。"""
    rect = page.rect
    zoom = min(RENDER_ZOOM, MAX_LONG_EDGE * 72 / max(rect.width, rect.height))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return base64.b64encode(pix.tobytes("png")).decode()


# ---------------- LLM 调用公共部分 ----------------
def _chat(client: OpenAI, messages: list) -> str:
    last_err = None
    for _ in range(LLM_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                timeout=LLM_TIMEOUT,
                extra_body={"think": False},
            )
            text = resp.choices[0].message.content or ""
            # 兜底：剥离可能残留的 thinking 段
            text = re.sub(r"<think.*?>.*?</think\s*>", "", text, flags=re.S)
            return text.strip()
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"    ! LLM 调用失败，重试… ({e})")
            time.sleep(2)
    raise RuntimeError(f"LLM 调用最终失败: {last_err}")


# ---------------- ③ VLM 识别手写笔记 ----------------
def recognize_handwriting(client: OpenAI, img_b64: str) -> str:
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": PROMPT_VISION},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
        ],
    }]
    return _chat(client, messages)


# ---------------- ⑤ 合并 Markdown ----------------
def merge_markdown(texts: list[str], notes_by_page: dict[int, str],
                   targets: list[int]) -> str:
    parts = []
    for i in targets:
        parts.append(f"<!-- 第 {i + 1} 页 -->\n\n{texts[i].strip()}")
        notes = notes_by_page.get(i, "").strip()
        if notes:
            quoted = "\n".join(f"> {line}" for line in notes.splitlines())
            parts.append(f"> ### 📝 老师手写笔记（第 {i + 1} 页）\n>\n{quoted}")
    return "\n\n---\n\n".join(parts) + "\n"


# ---------------- 缓存 ----------------
def _cache_get(cache: Path, name: str) -> str | None:
    f = cache / name
    return f.read_text(encoding="utf-8") if f.exists() else None


def _cache_put(cache: Path, name: str, content: str) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / name).write_text(content, encoding="utf-8")


# ---------------- 主流程 ----------------
def _make_client() -> OpenAI:
    """构造 OpenAI 客户端。

    本机开启了系统代理（127.0.0.1:7892），而 openai SDK 底层 httpx 默认
    trust_env=True 会让 localhost 请求也走代理，代理对 ollama 网关返回 502。
    这里显式传入 trust_env=False 的 http 客户端，直连本地网关。
    """
    http_client = httpx2.Client(trust_env=False, timeout=LLM_TIMEOUT)
    return OpenAI(base_url=BASE_URL, api_key="***", http_client=http_client)


def run(pdf_path: str, output_path: str, pages: list[int] | None) -> None:
    client = _make_client()
    cache = Path(CACHE_DIR)

    doc = pymupdf.open(pdf_path)
    total = doc.page_count
    targets = pages if pages else list(range(total))
    print(f"PDF 共 {total} 页，处理页码: {[p + 1 for p in targets]}")

    texts = extract_text_pages(doc)
    notes_by_page: dict[int, str] = {}

    for i in targets:
        print(f"[第 {i + 1} 页]")
        try:
            raw = _cache_get(cache, f"page_{i}_vision.md")
            if raw is None:
                raw = recognize_handwriting(
                    client, render_page_b64(doc.load_page(i)))
                _cache_put(cache, f"page_{i}_vision.md", raw)
            else:
                print("    视觉识别：使用缓存")

            if NO_NOTE_MARK in raw:
                print("    无手写笔记")
                continue

            # 方案1：跳过整理步骤，直接使用视觉识别结果
            notes_by_page[i] = raw
            print(f"    ✔ 提取到笔记 {len(raw)} 字")
        except Exception as e:  # noqa: BLE001
            notes_by_page[i] = f"<!-- 本页笔记提取失败: {e} -->"
            print(f"    ✘ 失败: {e}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        merge_markdown(texts, notes_by_page, targets), encoding="utf-8")
    doc.close()
    print(f"已写出: {out.resolve()}")


def main():
    ap = argparse.ArgumentParser(
        description="教材 PDF → Markdown（含手写笔记）")
    ap.add_argument("--pdf", default=PDF_PATH, help="PDF 路径")
    ap.add_argument("--out", default=OUTPUT_PATH, help="输出 Markdown 路径")
    ap.add_argument("--pages", default="",
                    help="仅处理指定页（1-based，逗号分隔），如 --pages 7 或 1,2,3")
    args = ap.parse_args()

    pages = [int(p) - 1 for p in args.pages.split(",") if p.strip()] or None
    run(args.pdf, args.out, pages)


if __name__ == "__main__":
    main()
