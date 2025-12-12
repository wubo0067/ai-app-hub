import asyncio
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import json
import os
import re
import glob
from pathlib import Path

COOKIE_FILE = "rh_cookies.json"
LOGIN_URL = "https://access.redhat.com/login"
DATA_DIR = "data"
FAILED_URLS_FILE = "failed_urls.json"

def extract_solution_id(url):
    """从 URL 中提取 solution ID"""
    match = re.search(r'/solutions/(\d+)', url)
    if match:
        return match.group(1)
    return "solution"

def get_output_directory_name(json_file):
    """从 JSON 文件名提取目录名，例如 solution-82.json -> solution-82"""
    basename = os.path.basename(json_file)
    return basename.replace(".json", "")

def load_failed_urls():
    """加载上次失败的 URL 记录"""
    if os.path.exists(FAILED_URLS_FILE):
        try:
            with open(FAILED_URLS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"xml_name": None, "url": None}
    return {"xml_name": None, "url": None}

def save_failed_url(xml_name, url):
    """保存失败的 URL 记录"""
    failed_info = {"xml_name": xml_name, "url": url}
    try:
        with open(FAILED_URLS_FILE, "w", encoding="utf-8") as f:
            json.dump(failed_info, f)
    except Exception as e:
        print(f"[!] 保存失败 URL 记录失败：{e}")

def remove_failed_urls_file():
    """删除失败 URL 记录文件"""
    if os.path.exists(FAILED_URLS_FILE):
        try:
            os.remove(FAILED_URLS_FILE)
        except Exception as e:
            print(f"[!] 删除失败 URL 记录文件失败：{e}")

async def save_cookies(context):
    """保存 cookie 到文件，过滤掉无效的 cookie"""
    try:
        cookies = await context.cookies()

        # 过滤掉无效的 cookie
        valid_cookies = []
        for cookie in cookies:
            # 跳过重启令牌和其他临时 cookie
            if cookie.get('name') in ['KC_RESTART']:
                continue
            valid_cookies.append(cookie)

        if valid_cookies:
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                json.dump(valid_cookies, f, indent=2)
            print(f"[+] 已保存 {len(valid_cookies)} 个有效 cookie 到 {COOKIE_FILE}")
            return True
        else:
            print("[!] 没有有效的 cookie 可保存")
            return False
    except Exception as e:
        print(f"[!] 保存 cookie 失败：{e}")
        return False

async def load_cookies(context):
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                cookies = json.load(f)

            # 过滤掉过期或无效的 cookies
            valid_cookies = []
            has_restart_token = False

            for cookie in cookies:
                # 检查 cookie 是否过期
                if cookie.get("expires", -1) > 0:
                    import time
                    if cookie["expires"] < time.time():
                        print(f"[!] 跳过过期 cookie: {cookie.get('name', 'unknown')}")
                        continue  # 跳过过期的 cookie

                # 检测重启令牌，它表明登录流程未完成
                if cookie.get("name") == "KC_RESTART":
                    has_restart_token = True
                    print("[!] 检测到未完成的登录流程 (KC_RESTART)")
                    continue

                valid_cookies.append(cookie)

            if has_restart_token:
                print("[!] Cookie 文件包含未完成的登录状态，建议重新登录")
                # 删除包含重启令牌的 cookie 文件
                try:
                    os.remove(COOKIE_FILE)
                    print("[!] 已删除无效的 cookie 文件")
                except:
                    pass
                return False

            if valid_cookies:
                await context.add_cookies(valid_cookies)
                print(f"[+] 已加载 {len(valid_cookies)} 个有效 cookie")
                return True
            else:
                print("[!] 没有找到有效的 cookie")
                return False

        except Exception as e:
            print(f"[!] 加载 cookie 失败：{e}")
            return False
    else:
        print(f"[!] Cookie 文件 {COOKIE_FILE} 不存在")
        return False

async def is_login_successful_by_cookies(context):
    """通过 cookies 判断是否登录成功"""
    try:
        cookies = await context.cookies()
        # 检查认证相关的 cookies
        cookie_names = {c.get("name") for c in cookies}
        auth_indicators = {"AUTH_SESSION_ID", "rh_common_id", "chrome_session_id", "KEYCLOAK_IDENTITY", "rh_jwt"}
        if auth_indicators & cookie_names:
            # 检查是否有重启令牌，如果有则表明登录未完成
            restart_cookies = [c for c in cookies if c.get("name") == "KC_RESTART"]
            if restart_cookies:
                return False  # 登录流程未完成
            return True
    except Exception:
        pass
    return False

async def has_access_denied(page):
    """检查是否遇到 Access Denied 页面"""
    try:
        content = await page.content()
        access_denied_indicators = [
            "Access Denied",
            "You are not authorized",
            "Permission denied",
            "403 Forbidden",
            "Access to this page is restricted"
        ]
        return any(indicator in content for indicator in access_denied_indicators)
    except Exception:
        return False

async def is_login_successful(page):
    """检查登录是否成功 - 通过检查是否存在 'Log in for full access' 文本"""
    try:
        content = await page.content()

        # 如果页面中不包含 "Log in for full access"，说明已登录成功
        if "Log in for full access" not in content:
            return True
        else:
            return False

    except Exception as e:
        print(f"[!] 检查登录状态出错：{e}")
        return False

async def needs_login(page):
    """检查页面是否需要登录"""
    try:
        content = await page.content()
        return "Log in for full access" in content
    except Exception:
        return False

async def fetch_solution(page, url):
    """爬取单个 solution 页面（复用浏览器）"""
    max_retries = 3

    for attempt in range(max_retries):
        # 访问目标页面
        await page.goto(url, wait_until="domcontentloaded")

        # 等待页面完全加载
        try:
            await page.wait_for_selector("article", timeout=25000)
        except Exception:
            await page.wait_for_load_state("load", timeout=25000)

        # 检查是否遇到 Access Denied
        if await has_access_denied(page):
            print(f"[!] 检测到 Access Denied 页面，跳过该页面")
            print(f"[!] URL: {url}")
            print(f"[!] 原因：用户权限不足或页面访问受限")
            # 直接跳过，不重试
            return "<html><body><h1>Access Denied</h1><p>无法访问该页面内容 - 权限不足</p></body></html>"

        # 检查页面是否需要登录
        if await needs_login(page):
            print("[!] 检测到页面需要登录，点击登录按钮...")

            # 查找并点击登录按钮
            login_button = await page.query_selector("text=/Log In|Log in|登录|Sign In/")
            if login_button:
                print("[!] 请在浏览器中输入账号密码...")
                await login_button.click()

                # 等待登录页面完全加载（给更多时间让登录页面加载）
                print("[*] 等待登录页面加载...")
                await asyncio.sleep(10)

                # 等待用户完成登录，固定等待时间，不做任何检测或跳转
                wait_time = 120  # 2 分钟，给用户充足时间
                print(f"[!] 等待用户登录，将等待 {wait_time} 秒...")
                print("[*] 请在弹出的浏览器窗口中完成登录操作")
                print("[*] 请勿关闭浏览器窗口")
                print(f"[*] 倒计时 {wait_time} 秒后自动继续...")

                # 简单等待，不做任何页面操作，避免打断用户登录
                for i in range(wait_time):
                    remaining = wait_time - i
                    if i % 10 == 0:  # 每 10 秒显示一次剩余时间
                        print(f"\n[*] 剩余等待时间：{remaining} 秒", end="", flush=True)
                    else:
                        print(".", end="", flush=True)
                    await asyncio.sleep(1)

                print(f"\n[+] 等待完成，假设登录已完成，继续执行...")

                # 保存当前 cookies 状态
                print("\n[+] 保存登录状态...")
                await save_cookies(page.context)

                # 确保页面在正确位置
                await page.goto(url, wait_until="domcontentloaded")
                try:
                    await page.wait_for_selector("article", timeout=25000)
                except Exception:
                    await page.wait_for_load_state("load", timeout=25000)
            else:
                print("[!] 未找到登录按钮，跳过登录流程")

        # 成功获取页面内容
        html = await page.content()

        # 保存当前 cookie 状态
        await save_cookies(page.context)

        return html

    # 如果所有重试都失败
    return "<html><body><h1>页面获取失败</h1><p>多次重试后仍无法获取页面内容</p></body></html>"

def extract_article_text(html):
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article")
    if not article:
        print("未找到文章内容，请检查是否权限或页面结构变更。")
        return ""
    text = article.get_text(separator="\n", strip=True)
    return text

def html_to_markdown(html):
    """将 HTML 页面转换为 Markdown 格式"""
    soup = BeautifulSoup(html, "html.parser")

    # 检查是否是 Access Denied 页面
    if "Access Denied" in html:
        return "# Access Denied\n\n❌ **页面访问被拒绝**\n\n该页面因权限限制无法访问。可能的原因：\n- 用户权限不足\n- 页面需要特殊访问权限\n- 内容仅限特定用户访问\n\n**状态**：跳过该页面"

    # 检查是否是页面获取失败
    if "页面获取失败" in html:
        return "# 页面获取失败\n\n❌ **无法获取页面内容**\n\n多次尝试后仍无法正常加载页面内容。\n\n**状态**：获取失败"

    # 移除不必要的脚本和样式标签
    for script in soup(["script", "style"]):
        script.decompose()

    # 提取标题
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)

    # 提取文章内容
    article = soup.find("article")
    if not article:
        return "# 文章内容不可用\n\n页面需要登录或内容已变更。"

    markdown = f"# {title}\n\n" if title else ""

    # 处理文章中的各个元素
    for element in article.find_all():
        if element.name == "h1":
            markdown += f"\n# {element.get_text(strip=True)}\n\n"
        elif element.name == "h2":
            markdown += f"\n## {element.get_text(strip=True)}\n\n"
        elif element.name == "h3":
            markdown += f"\n### {element.get_text(strip=True)}\n\n"
        elif element.name == "h4":
            markdown += f"\n#### {element.get_text(strip=True)}\n\n"
        elif element.name == "p":
            text = element.get_text(strip=True)
            if text and not element in article.find_all("li"):  # 避免重复
                markdown += f"{text}\n\n"
        elif element.name == "ul":
            for li in element.find_all("li", recursive=False):
                markdown += f"- {li.get_text(strip=True)}\n"
            markdown += "\n"
        elif element.name == "ol":
            for i, li in enumerate(element.find_all("li", recursive=False), 1):
                markdown += f"{i}. {li.get_text(strip=True)}\n"
            markdown += "\n"
        elif element.name == "code":
            markdown += f"`{element.get_text(strip=True)}`"
        elif element.name == "pre":
            code = element.get_text(strip=True)
            markdown += f"\n```\n{code}\n```\n\n"

    return markdown

async def main():
    """从 solution-urls.json 读取 URL 并批量爬取"""

    # 读取 solution-urls.json 文件
    urls_file = "solution-urls.json"
    if not os.path.exists(urls_file):
        print(f"[!] 未找到 {urls_file} 文件")
        return

    print(f"[+] 读取 {urls_file} 文件\n")

    try:
        with open(urls_file, "r", encoding="utf-8") as f:
            solutions_urls = json.load(f)
    except Exception as e:
        print(f"[!] 读取 {urls_file} 文件失败：{e}")
        return

    if not solutions_urls:
        print(f"[!] {urls_file} 文件为空")
        return

    print(f"[+] 找到 {len(solutions_urls)} 个 solution XML 文件\n")

    # 加载上次失败的 URL 记录
    failed_info = load_failed_urls()
    resume_xml = failed_info.get("xml_name")
    resume_url = failed_info.get("url")

    if resume_xml:
        print(f"[!] 检测到上次中断的位置")
        print(f"    XML: {resume_xml}")
        print(f"    URL: {resume_url}")
        print(f"[*] 将从此处继续...\n")

    # 启动单一浏览器实例
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        cookies_loaded = await load_cookies(context)
        page = await context.new_page()

        if cookies_loaded:
            print("[+] Cookie 加载成功，将尝试使用现有登录状态")
        else:
            print("[!] 无有效 cookie，可能需要重新登录")

        # 设置页面默认超时
        page.set_default_timeout(120000)  # 2 分钟
        page.set_default_navigation_timeout(120000)

        skip_until_xml = resume_xml
        skip_until_url = resume_url
        found_resume_point = not resume_xml  # 如果没有恢复点，则立即开始处理

        for xml_name, urls in solutions_urls.items():
            # 如果还没找到恢复点，跳过当前 XML
            if not found_resume_point:
                if xml_name == skip_until_xml:
                    found_resume_point = True
                    print(f"[*] 找到中断点，从 {xml_name} 开始继续处理\n")
                else:
                    print(f"[*] 跳过 {xml_name}（在恢复点之前）")
                    continue

            # 创建输出目录 data/solution-*/
            output_path = os.path.join(DATA_DIR, xml_name.replace(".xml", ""))
            Path(output_path).mkdir(parents=True, exist_ok=True)

            print(f"[*] 处理：{xml_name}")

            # 处理每个 URL
            skip_urls_before = False
            for idx, url in enumerate(urls, 1):
                # 如果需要跳过前面的 URL
                if skip_until_url and not skip_urls_before:
                    if url == skip_until_url:
                        skip_urls_before = True
                        print(f"  [*] 找到恢复 URL，从此处继续\n")
                    else:
                        print(f"  [{idx}/{len(urls)}] 跳过 URL（在恢复点之前）")
                        continue

                solution_id = extract_solution_id(url)
                markdown_file = os.path.join(output_path, f"{solution_id}.md")

                # 检查文件是否已存在
                if os.path.exists(markdown_file):
                    print(f"  [{idx}/{len(urls)}] solution-{solution_id} 已存在，跳过")
                    continue

                print(f"  [{idx}/{len(urls)}] 正在爬取 solution-{solution_id}...")

                try:
                    # 爬取页面
                    html = await fetch_solution(page, url)

                    # 转换为 Markdown
                    markdown_content = html_to_markdown(html)

                    if markdown_content:
                        # 保存 Markdown 文件
                        with open(markdown_file, "w", encoding="utf-8") as f:
                            f.write(markdown_content)

                        # 根据内容类型显示不同的日志
                        if "Access Denied" in markdown_content:
                            print(f"      [!] Access Denied - 已保存错误信息：{markdown_file}")
                        elif "页面获取失败" in markdown_content:
                            print(f"      [!] 页面获取失败 - 已保存错误信息：{markdown_file}")
                        else:
                            print(f"      [+] 已保存：{markdown_file}")
                    else:
                        print(f"      [!] 无法提取文章内容")

                except Exception as e:
                    print(f"      [!] 爬取失败：{e}")
                    print(f"[!] 保存失败的 URL 记录...")
                    save_failed_url(xml_name, url)
                    await browser.close()
                    return

                # 每抓取 10 个页面就 sleep 1 秒
                if idx % 10 == 0:
                    print(f"  [*] 已抓取 {idx} 个页面，休息 1 秒...")
                    await asyncio.sleep(1)

            print(f"[+] {xml_name} 处理完成\n")
            skip_until_url = None  # 处理完当前 XML 后，清除 URL 恢复点

        # 所有处理完成，删除失败 URL 记录文件
        remove_failed_urls_file()
        print("[+] 所有 solution 处理完成！")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
