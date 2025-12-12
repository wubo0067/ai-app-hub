import os
import json
import requests
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

# 配置
SOLUTIONS_DIR = "solutions"
SOLUTIONS_TXT = "solutions.txt"
OUTPUT_JSON = "solution-urls.json"

def ensure_dir(directory):
    """确保目录存在"""
    Path(directory).mkdir(parents=True, exist_ok=True)

def download_xml_files():
    """下载 solutions.txt 中每一行的 XML 文件"""
    ensure_dir(SOLUTIONS_DIR)

    if not os.path.exists(SOLUTIONS_TXT):
        print(f"[!] 文件 {SOLUTIONS_TXT} 不存在")
        return []

    downloaded_files = []

    with open(SOLUTIONS_TXT, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    print(f"[+] 找到 {len(urls)} 个 URL\n")

    for idx, url in enumerate(urls, 1):
        if not url:
            continue

        # 提取文件名
        filename = url.split("/")[-1]
        filepath = os.path.join(SOLUTIONS_DIR, filename)

        # 如果文件已存在，跳过
        if os.path.exists(filepath):
            print(f"[{idx}/{len(urls)}] {filename} 已存在，跳过")
            downloaded_files.append(filepath)
            continue

        try:
            print(f"[{idx}/{len(urls)}] 正在下载 {filename}...", end="", flush=True)
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(response.text)

            print(" [成功]")
            downloaded_files.append(filepath)

        except Exception as e:
            print(f" [失败：{e}]")
            continue

    print(f"\n[+] 下载完成，共 {len(downloaded_files)} 个文件\n")
    return downloaded_files

def parse_xml_files(xml_files):
    """解析 XML 文件，提取 loc 中的 URL"""
    all_urls = {}

    print(f"[+] 开始解析 XML 文件\n")

    for idx, filepath in enumerate(xml_files, 1):
        filename = os.path.basename(filepath)
        print(f"[{idx}/{len(xml_files)}] 正在解析 {filename}...", end="", flush=True)

        try:
            tree = ET.parse(filepath)
            root = tree.getroot()

            # XML 命名空间
            namespaces = {'': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

            # 查找所有 loc 元素
            urls = []
            for loc in root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc'):
                if loc.text:
                    urls.append(loc.text)

            # 如果没找到带命名空间的，尝试不带命名空间
            if not urls:
                for loc in root.findall('.//loc'):
                    if loc.text:
                        urls.append(loc.text)

            all_urls[filename] = urls
            print(f" [找到 {len(urls)} 个 URL]")

        except Exception as e:
            print(f" [失败：{e}]")
            all_urls[filename] = []
            continue

    print(f"\n[+] 解析完成\n")
    return all_urls

def save_urls_to_json(all_urls):
    """将提取的 URL 保存到 JSON 文件"""
    print(f"[+] 保存 URL 到 {OUTPUT_JSON}...", end="", flush=True)

    # 展平所有 URL（统计总数）
    total_urls = sum(len(urls) for urls in all_urls.values())

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_urls, f, ensure_ascii=False, indent=2)

    print(f" [成功]")
    print(f"[+] 共提取 {total_urls} 个 URL 从 {len(all_urls)} 个文件")

def main():
    print("=" * 60)
    print("Solution URLs 下载和解析工具")
    print("=" * 60 + "\n")

    # 下载 XML 文件
    xml_files = download_xml_files()

    if not xml_files:
        print("[!] 没有可解析的 XML 文件")
        return

    # 解析 XML 文件
    all_urls = parse_xml_files(xml_files)

    # 保存到 JSON
    save_urls_to_json(all_urls)

    print("\n" + "=" * 60)
    print("[+] 所有操作完成！")
    print("=" * 60)

if __name__ == "__main__":
    main()
