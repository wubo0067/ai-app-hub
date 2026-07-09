#!/u,r/bin/env python3,,,
# -*- coding: utf-8 -*-
"""
网站文件下载爬虫
=================
功能：
    1. 读取配置文件（config.ini），获取目标网址、登录信息、下载目录等
    2. 支持表单登录（保持会话 Cookie）
    3. 递归爬取指定网址下的所有页面链接
    4. 下载指定后缀的文件（如 docx, pdf 等）
    5. 按照网站上的目录层级，在本地创建对应子目录并保存文件

用法：
    python3 web_downloader.py [配置文件路径，默认 config.ini]

详细使用说明见 README.md
"""

import configparser
import logging
import os
import sys
import time
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup

# ------------------------------------------------------------------
# 日志配置
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("crawler.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


class WebFileDownloader:
    def __init__(self, config_path: str):
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"配置文件不存在：{config_path}")

        self.config = configparser.ConfigParser(interpolation=None)
        self.config.read(config_path, encoding="utf-8")

        # --- site ---
        self.start_url = self.config.get("site", "start_url").strip()
        self.base_url = self.config.get("site", "base_url").strip()
        self.base_domain = urlparse(self.base_url).netloc

        # --- login ---
        self.enable_login = self.config.getboolean("login", "enable_login", fallback=False)
        self.login_url = self.config.get("login", "login_url", fallback="").strip()
        self.login_post_url = self.config.get("login", "login_post_url", fallback="").strip() or self.login_url
        self.username_field = None
        self.password_field = None
        self.username = self.config.get("login", "username", fallback="").strip()
        self.password = self.config.get("login", "password", fallback="").strip()
        extra_fields_raw = self.config.get("login", "extra_fields", fallback="").strip()
        self.extra_fields = self._parse_extra_fields(extra_fields_raw)
        # verify_success_text / verify_fail_text：该页面上能分别标志"已登录"/"未登录"的文本片段
        self.verify_url = self.config.get("login", "verify_url", fallback="").strip()
        self.verify_success_text = self.config.get("login", "verify_success_text", fallback="").strip()
        self.verify_fail_text = self.config.get("login", "verify_fail_text", fallback="").strip()

        # --- download ---
        exts = self.config.get("download", "extensions", fallback="docx").strip()
        self.extensions = tuple("." + e.strip().lower().lstrip(".") for e in exts.split(",") if e.strip())
        self.save_dir = Path(self.config.get("download", "save_dir", fallback="./downloads")).resolve()

        # --- crawler ---
        self.max_depth = self.config.getint("crawler", "max_depth", fallback=5)
        self.delay = self.config.getfloat("crawler", "delay_seconds", fallback=0.5)
        self.timeout = self.config.getint("crawler", "timeout", fallback=15)
        self.skip_existing = self.config.getboolean("crawler", "skip_existing", fallback=True)
        self.user_agent = self.config.get(
            "crawler", "user_agent",
            fallback="Mozilla/5.0 (Windows NT 10.0; Win64; x64) WebFileDownloader/1.0",
        )
        # proxy config (用于 playwright 下载浏览器时的代理)
        self.proxy_enabled = self.config.getboolean("crawler", "proxy_enabled", fallback=False)
        self.proxy_http = self.config.get("crawler", "proxy_http", fallback="").strip()
        self.proxy_https = self.config.get("crawler", "proxy_https", fallback="").strip()

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

        self.visited_pages = set()
        self.downloaded_files = set()
        self.download_count = 0
        self.fail_count = 0

    # ------------------------------------------------------------------
    @staticmethod
    def _attr_str(value) -> str | None:
        """将 bs4 的属性值（可能是 str / list / None）安全转换为 str | None，
        用于消除 Pylance 对 urljoin 等函数的类型告警。"""
        if value is None:
            return None
        if isinstance(value, list):
            return value[0] if value else None
        return str(value)

    @staticmethod
    def _parse_extra_fields(raw: str) -> dict:
        result = {}
        if not raw:
            return result
        for pair in raw.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                result[k.strip()] = v.strip()
        return result


    def _detect_login_fields(self, form):
        """
        自动识别表单中的用户名 / 密码字段。

        修复点：
        - 【问题 3】多个 type="password" 时不再简单"后者覆盖前者"，
          优先选择字段名不含"确认/再次/confirm/repeat"等含义的那一个。
        - 【问题 2】用户名候选字段加入黑名单过滤（验证码/短信验证码等），
          避免 fallback 误抓到无关文本框；并引入"打分 + 与密码框的
          DOM 距离"作为兜底排序依据，而不是简单取第一个非隐藏字段。
        """
        user_keys = ["user", "username", "login", "account", "email", "mobile", "phone", "userid", "loginname"]
        user_cn_keys = ["用户名", "账号", "邮箱", "手机"]
        blacklist_keys = ["captcha", "verify", "vcode", "yzm", "code", "otp",
                           "验证码", "短信验证码", "图形验证码", "验证"]
        confirm_keys = ["confirm", "repeat", "again", "reenter", "re-enter",
                         "确认", "再次", "二次"]

        inputs = form.find_all("input")

        # --- 第一步：确定密码字段（处理多密码框场景）---
        password_candidates = []  # [(index, name, blob)]
        for idx, inp in enumerate(inputs):
            t = (self._attr_str(inp.get("type")) or "text").lower()
            name = self._attr_str(inp.get("name"))
            if not name or t != "password":
                continue
            blob = " ".join([
                (self._attr_str(inp.get("name")) or "").lower(),
                (self._attr_str(inp.get("id")) or "").lower(),
                (self._attr_str(inp.get("placeholder")) or "").lower(),
            ])
            password_candidates.append((idx, name, blob))

        password = None
        password_idx = None
        if password_candidates:
            # 优先选不含"确认/再次"等语义的密码框
            primary = next((c for c in password_candidates if not any(k in c[2] for k in confirm_keys)), None)
            if primary is None:
                primary = password_candidates[0]
            password_idx, password, _ = primary
            if len(password_candidates) > 1:
                skipped = [c[1] for c in password_candidates if c[1] != password]
                log.warning(f"检测到多个密码输入框 {[c[1] for c in password_candidates]}，"
                            f"已选用 '{password}'（忽略 {skipped}），如判断有误请在 config.ini 中"
                            f"通过 extra_fields 手动指定。")

        # --- 第二步：收集用户名候选字段（排除隐藏/按钮/密码/黑名单字段）---
        candidates = []  # (score, -distance_to_password, name)
        for idx, inp in enumerate(inputs):
            t = (self._attr_str(inp.get("type")) or "text").lower()
            name = self._attr_str(inp.get("name"))
            if not name or t == "password":
                continue
            if t in ("hidden", "submit", "button", "checkbox", "radio", "file"):
                continue

            id_val = self._attr_str(inp.get("id")) or ""
            placeholder = self._attr_str(inp.get("placeholder")) or ""
            blob = " ".join([name.lower(), id_val.lower(), placeholder.lower()])

            if any(k in blob for k in blacklist_keys):
                continue  # 明显是验证码一类的无关字段，跳过

            score = 0
            if t == "email":
                score += 3
            if any(k in blob for k in user_keys):
                score += 3
            if any(x in placeholder for x in user_cn_keys):
                score += 3

            distance = abs(idx - password_idx) if password_idx is not None else 0
            candidates.append((score, -distance, idx, name))

        if not candidates:
            return None, password

        # 分数优先；分数相同则取离密码框最近的字段（更符合大多数登录表单的
        # "用户名紧挨在密码框上方"的布局），仍相同则保留 DOM 中靠前的一个
        candidates.sort(key=lambda c: (c[0], c[1], -c[2]), reverse=True)
        username = candidates[0][3]
        if not any(c[0] > 0 for c in candidates):
            log.warning(f"用户名字段未命中任何关键词，按兜底规则选用 '{username}'，"
                        f"建议人工核实是否正确。")
        return username, password

    # ------------------------------------------------------------------
    def login(self):
        """使用 Playwright 浏览器自动化登录，支持 JS 动态渲染的 SPA 登录页面。

        登录流程：
        1. 启动 Chromium 浏览器，打开登录页面
        2. 自动填写用户名、密码，点击登录按钮
        3. 等待登录完成（URL 跳转离开登录页域）
        4. 将浏览器中的 Cookie 导入到 requests.Session
        5. 关闭浏览器

        若自动填写失败，会在终端提示用户手动在浏览器中完成登录后按 Enter 继续。
        """
        if not self.enable_login:
            log.info("未启用登录，跳过登录步骤。")
            return

        # --- 检查 Playwright 是否可用，并自动安装 Chromium 浏览器 ---
        try:
            from playwright.sync_api import sync_playwright
            from playwright.sync_api import TimeoutError as PwTimeout  # noqa: F811
        except ImportError:
            self._try_install_playwright_package()
            try:
                from playwright.sync_api import sync_playwright
                from playwright.sync_api import TimeoutError as PwTimeout  # noqa: F811
            except ImportError:
                log.error("playwright 安装失败，请手动执行：pip install playwright")
                input("\n按 Enter 退出...")
                sys.exit(1)

        self._ensure_playwright_browser()

        headless = self.config.getboolean("login", "browser_headless", fallback=False)
        browser_timeout_ms = self.config.getint("login", "browser_timeout", fallback=120) * 1000

        log.info(f"启动 Chromium 浏览器（headless={headless}）...")

        _PwTimeout = PwTimeout  # 本地引用，避免嵌套作用域问题
        # 修正 PyInstaller 打包后的路径问题：强制 Playwright 从标准缓存目录查找浏览器
        pw_browsers_path = str(Path.home() / "AppData" / "Local" / "ms-playwright")
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = pw_browsers_path
        log.info(f"已设置 PLAYWRIGHT_BROWSERS_PATH={pw_browsers_path}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=self.user_agent,
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            # --- 步骤 1：打开登录页面 ---
            log.info(f"正在打开登录页面：{self.login_url}")
            page.goto(self.login_url, wait_until="domcontentloaded", timeout=browser_timeout_ms)
            # 再等一次 networkidle，确保 JS 渲染完成
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except _PwTimeout:
                log.warning("登录页 networkidle 超时，继续尝试...")

            # --- 步骤 2：自动填写表单 ---
            auto_ok = self._browser_fill_login(page, browser_timeout_ms)

            if not auto_ok:
                log.info("=" * 60)
                log.info("⚠ 未能完全自动填写/提交登录表单。")
                log.info("  请在浏览器窗口中手动完成登录操作。")
                log.info("  登录完成后，回到此终端按 Enter 继续...")
                log.info("=" * 60)
                input()

            # --- 步骤 3：等待登录完成 ---
            self._browser_wait_login(page, browser_timeout_ms)

            # --- 步骤 4：转移 Cookie ---
            cookies = context.cookies()
            for c in cookies:
                self.session.cookies.set(
                    c.get("name", ""), c.get("value", ""),
                    domain=c.get("domain", ""),
                    path=c.get("path", "/"),
                )
            log.info(f"已从浏览器导入 {len(cookies)} 个 Cookie 到下载会话")

            # --- 可选：校验页面验证登录状态 ---
            if self.verify_url:
                log.info(f"正在访问校验页面确认登录状态：{self.verify_url}")
                try:
                    page.goto(self.verify_url, wait_until="networkidle", timeout=30000)
                    # 刷新 Cookie（校验页可能设置新的）
                    for c in context.cookies():
                        self.session.cookies.set(
                            c.get("name", ""), c.get("value", ""),
                            domain=c.get("domain", ""),
                            path=c.get("path", "/"),
                        )
                    # 检查校验文本
                    body = page.content()
                    if self.verify_fail_text and self.verify_fail_text in body:
                        log.warning(f"校验页面出现 fail 标记，登录可能未成功！")
                    elif self.verify_success_text:
                        if self.verify_success_text in body:
                            log.info("校验页面确认登录成功。")
                        else:
                            log.warning("校验页面未出现 success 标记，登录可能未成功。")
                except Exception as e:
                    log.warning(f"校验页面访问失败：{e}")

            browser.close()

        log.info("浏览器登录完成，Cookie 已就绪。")

    # ------------------------------------------------------------------
    def _browser_fill_login(self, page, timeout_ms: int) -> bool:
        """在浏览器页面中自动填写用户名、密码并点击登录按钮。

        Returns:
            True 表示全部自动完成（填用户、填密码、点登录），False 表示部分或全部失败。
        """
        from playwright.sync_api import TimeoutError as PwTimeout  # noqa: F811

        # 等待密码框出现（说明 JS 已渲染出登录表单）
        try:
            page.wait_for_selector('input[type="password"]', timeout=15000)
        except PwTimeout:
            log.warning("等待密码输入框超时，登录表单可能未渲染。")
            return False

        # --- 填写用户名 ---
        filled_user = False
        user_selectors = [
            'input[type="text"]',
            'input[type="email"]',
            'input[type="tel"]',
            'input:not([type])',
            'input[name*="user" i]',
            'input[name*="account" i]',
            'input[name*="login" i]',
            'input[name*="phone" i]',
            'input[id*="user" i]',
            'input[id*="account" i]',
        ]
        for sel in user_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_enabled() and el.is_visible():
                    el.click()
                    el.fill("")
                    el.fill(self.username)
                    log.info(f"已填入用户名 → {sel}")
                    filled_user = True
                    break
            except Exception:
                continue

        if not filled_user:
            log.warning("未找到可用的用户名输入框。")

        # --- 填写密码 ---
        filled_pwd = False
        pwd_selectors = [
            'input[type="password"]',
            'input[name*="password" i]',
            'input[name*="pass" i]',
            'input[id*="password" i]',
        ]
        for sel in pwd_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_enabled() and el.is_visible():
                    el.click()
                    el.fill("")
                    el.fill(self.password)
                    log.info(f"已填入密码 → {sel}")
                    filled_pwd = True
                    break
            except Exception:
                continue

        if not filled_pwd:
            log.warning("未找到可用的密码输入框。")

        # --- 点击登录按钮 ---
        clicked = False
        btn_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("登录")',
            'button:has-text("登 录")',
            'a:has-text("登录")',
            'button:has-text("Login")',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
            'span:has-text("登录")',
            'div[role="button"]:has-text("登录")',
        ]
        for sel in btn_selectors:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_enabled() and btn.is_visible():
                    btn.click()
                    log.info(f"已点击登录按钮 → {sel}")
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            log.warning("未找到可点击的登录按钮。")

        # 短暂等待让点击生效
        page.wait_for_timeout(2000)
        return filled_user and filled_pwd and clicked

    # ------------------------------------------------------------------
    def _browser_wait_login(self, page, timeout_ms: int):
        """等待登录跳转完成（当前 URL 不再包含登录页特征）。"""
        from playwright.sync_api import TimeoutError as PwTimeout  # noqa: F811

        login_netloc = urlparse(self.login_url).netloc
        log.info("等待登录跳转...")

        try:
            # 等待 URL 的域名离开登录页域名
            page.wait_for_function(
                """(loginHost) => window.location.hostname !== loginHost""",
                arg=login_netloc,
                timeout=timeout_ms,
            )
            log.info(f"登录跳转完成，当前页面：{page.url}")
        except PwTimeout:
            log.warning(f"等待登录跳转超时（{timeout_ms / 1000:.0f}s），将尝试继续。"
                        f"当前 URL：{page.url}")

        # 等待页面稳定
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PwTimeout:
            pass

    # ------------------------------------------------------------------
    @staticmethod
    def _find_system_python() -> str | None:
        """探测系统上可用的 Python 解释器（不依赖 PATH）。

        按优先级尝试：python3 → python → 常见的安装路径。
        返回可用的解释器路径，或 None。
        """
        candidates = ["python", "python3", "py", "py -3"]
        # 常见 Windows 安装路径
        from pathlib import Path
        for ver in ["312", "311", "310", "39", "313"]:
            for root in [Path.home() / "AppData" / "Local" / "Programs" / "Python",
                         Path("C:/Program Files")]:
                p = root / f"Python{ver}" / "python.exe"
                if p.exists():
                    candidates.insert(0, str(p))
        import subprocess as _sp
        for cmd in candidates:
            try:
                result = _sp.run(
                    [cmd, "--version"], capture_output=True, text=True, timeout=10,
                    creationflags=0x08000000 if sys.platform == "win32" else 0,  # CREATE_NO_WINDOW
                )
                if result.returncode == 0 and "Python" in (result.stdout or result.stderr):
                    return cmd
            except Exception:
                continue
        return None

    def _try_install_playwright_package(self):
        """尝试自动安装 playwright Python 包（若配置了代理则使用代理）。"""
        log.info("playwright 未安装，尝试自动安装...")
        py = WebFileDownloader._find_system_python()
        if not py:
            log.error("未找到系统 Python，无法自动安装。请手动执行：pip install playwright")
            return
        # 构造带代理的 pip 命令（若启用代理）
        pip_args = [py, "-m", "pip", "install", "playwright"]
        if self.proxy_enabled and self.proxy_http:
            pip_args.extend(["--proxy", self.proxy_http])
            log.info(f"pip 将使用代理：{self.proxy_http}")
        import subprocess as _sp
        try:
            result = _sp.run(
                pip_args,
                capture_output=True, text=True, timeout=300,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
            if result.returncode == 0:
                log.info("playwright 安装成功。")
            else:
                log.error(f"pip install 失败：{result.stderr.strip()[-500:]}")
        except Exception as e:
            log.error(f"自动安装 playwright 失败：{e}")

    def _ensure_playwright_browser(self):
        """确保 Playwright Chromium 浏览器已安装；若未安装则自动下载（约 300MB）。

        若配置了代理（proxy_enabled=true），会在进程环境中设置 HTTP_PROXY / HTTPS_PROXY，
        使得 playwright 能通过代理下载 Chromium 浏览器。

        支持两种运行模式：
        - 脚本模式：通过 subprocess 调用系统 Python 的 playwright CLI
        - EXE 模式（PyInstaller）：通过 playwright 进程内 API 直接下载
        """
        from pathlib import Path
        pw_dir = Path.home() / "AppData" / "Local" / "ms-playwright"
        if pw_dir.exists() and any(pw_dir.glob("chromium-*")):
            return  # 已安装

        # 设置代理环境变量（对当前进程及其子进程均生效）
        if self.proxy_enabled:
            if self.proxy_http and "HTTP_PROXY" not in os.environ:
                os.environ["HTTP_PROXY"] = self.proxy_http
                log.info(f"已设置 HTTP_PROXY={self.proxy_http}")
            if self.proxy_https and "HTTPS_PROXY" not in os.environ:
                os.environ["HTTPS_PROXY"] = self.proxy_https
                log.info(f"已设置 HTTPS_PROXY={self.proxy_https}")
            # 企业代理通常做 SSL 中间人解密（自签名证书），
            # 需要让 Node.js（playwright 下载器底层）跳过证书验证
            if "NODE_TLS_REJECT_UNAUTHORIZED" not in os.environ:
                os.environ["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
                log.info("已设置 NODE_TLS_REJECT_UNAUTHORIZED=0（跳过代理证书验证）")

        log.info("=" * 60)
        log.info("未检测到 Chromium 浏览器（Playwright 需要它来模拟登录）。")
        log.info("正在自动下载（约 300MB，首次运行只需一次，请耐心等待）...")
        log.info("=" * 60)

        # --- 尝试 1：进程内 playwright API（兼容 EXE 打包模式）---
        try:
            from playwright.__main__ import main as _pw_main
            import sys as _sys
            original_argv = _sys.argv[:]
            try:
                _sys.argv = ["playwright", "install", "chromium"]
                _pw_main()
                # playwright CLI 内部调用 sys.exit(0) 成功时抛出 SystemExit(0)
            except SystemExit as e:
                if e.code == 0:
                    log.info("Chromium 浏览器安装成功！")
                    return
                else:
                    log.warning(f"playwright 进程内安装返回非零退出码：{e.code}")
            except Exception as e:
                log.warning(f"playwright 进程内安装异常：{e}")
            finally:
                _sys.argv = original_argv
        except ImportError:
            log.info("playwright.__main__ 不可用，将尝试子进程方式...")
        except Exception as e:
            log.warning(f"playwright 进程内安装方式失败：{e}")

        # --- 尝试 2：通过 subprocess 调用系统 Python（脚本模式）---
        import subprocess as _sp
        py = WebFileDownloader._find_system_python()
        if py:
            env = os.environ.copy()
            try:
                log.info(f"通过 {py} 下载 Chromium 浏览器...")
                result = _sp.run(
                    [py, "-m", "playwright", "install", "chromium"],
                    capture_output=False,
                    timeout=600,
                    env=env,
                )
                if result.returncode == 0:
                    log.info("Chromium 浏览器安装成功！")
                    return
            except Exception as e:
                log.warning(f"通过系统 Python 下载失败：{e}")

        # --- 尝试 3：兜底，直接调用 playwright CLI ---
        cli_candidates = ["playwright", "npx playwright", "python -m playwright"]
        for cmd in cli_candidates:
            try:
                parts = cmd.split()
                result = _sp.run(
                    parts + ["install", "chromium"],
                    capture_output=False, timeout=600,
                    creationflags=0x08000000 if sys.platform == "win32" else 0,
                )
                if result.returncode == 0:
                    log.info("Chromium 浏览器安装成功！")
                    return
            except Exception:
                continue

        # 全部失败
        log.error("=" * 60)
        log.error("❌ 自动下载 Chromium 浏览器失败。请手动执行以下命令后重试：")
        log.error("   在终端中依次执行：")
        log.error("   $env:HTTP_PROXY='http://proxy.xfusion.com:8080'")
        log.error("   $env:HTTPS_PROXY='http://proxy.xfusion.com:8080'")
        log.error("   playwright install chromium")
        log.error("=" * 60)
        input("\n按 Enter 退出...")
        sys.exit(1)

    # ------------------------------------------------------------------
    @staticmethod
    def _save_debug_html(html_text: str, filename: str) -> str:
        """保存 HTML 调试文件。"""
        try:
            debug_path = Path(filename).resolve()
            debug_path.write_text(html_text, encoding="utf-8", errors="ignore")
            return str(debug_path)
        except OSError as e:
            log.error(f"保存调试 HTML 失败：{e}")
            return "(保存失败)"

    # ------------------------------------------------------------------
    def _is_same_domain(self, url: str) -> bool:
        return urlparse(url).netloc == self.base_domain

    def _is_target_file(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return path.endswith(self.extensions)

    def _local_path_for_file(self, url: str) -> Path:
        """根据 URL 路径，在 save_dir 下生成对应的本地保存路径（保留目录层级）"""
        path = unquote(urlparse(url).path)  # 解码中文文件名等
        # 去除开头的斜杠，转为相对路径
        rel_path = path.lstrip("/")
        # 防止路径穿越攻击 (../)
        rel_path = re.sub(r"\.\.[\\/]", "", rel_path)
        local_path = self.save_dir / rel_path
        return local_path

    # ------------------------------------------------------------------
    def download_file(self, url: str):
        if url in self.downloaded_files:
            return
        local_path = self._local_path_for_file(url)

        if self.skip_existing and local_path.exists():
            log.info(f"已存在，跳过：{local_path}")
            self.downloaded_files.add(url)
            return

        local_path.parent.mkdir(parents=True, exist_ok=True)

        log.info(f"下载中：{url}")
        try:
            resp = self.session.get(url, timeout=self.timeout, stream=True)
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            self.downloaded_files.add(url)
            self.download_count += 1
            log.info(f"已保存：{local_path}")
        except requests.RequestException as e:
            self.fail_count += 1
            log.error(f"下载失败 [{url}]: {e}")
        finally:
            time.sleep(self.delay)

    # ------------------------------------------------------------------
    def crawl(self, url: str, depth: int = 0):
        if depth > self.max_depth:
            return
        if url in self.visited_pages:
            return
        if not self._is_same_domain(url):
            return

        self.visited_pages.add(url)

        # 如果这个链接本身就是目标文件，直接下载，不当作页面解析
        if self._is_target_file(url):
            self.download_file(url)
            return

        log.info(f"[深度 {depth}] 正在爬取页面：{url}")
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error(f"访问页面失败 [{url}]: {e}")
            return

        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            # 非 HTML 页面（可能是直接的文件流），按扩展名再判断一次
            if self._is_target_file(url):
                self.download_file(url)
            return

        soup = BeautifulSoup(resp.text, "lxml")
        links = set()
        for tag in soup.find_all(["a", "link"]):
            href = self._attr_str(tag.get("href"))
            if href:
                links.add(urljoin(url, href))
        for tag in soup.find_all(["img", "script", "source"]):
            src = self._attr_str(tag.get("src"))
            if src:
                links.add(urljoin(url, src))

        time.sleep(self.delay)

        for link in links:
            link = link.split("#")[0]  # 去掉锚点
            if not link or not self._is_same_domain(link):
                continue
            if self._is_target_file(link):
                self.download_file(link)
            else:
                # 只递归到看起来是目录/页面的链接，避免爬取无关的静态资源
                if self._looks_like_page(link):
                    self.crawl(link, depth + 1)

    @staticmethod
    def _looks_like_page(url: str) -> bool:
        path = urlparse(url).path.lower()
        static_exts = (
            ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
            ".woff", ".woff2", ".ttf", ".mp4", ".mp3", ".zip", ".rar",
        )
        return not path.endswith(static_exts)

    # ------------------------------------------------------------------
    def run(self):
        log.info("=" * 60)
        log.info("网站文件下载爬虫启动")
        log.info(f"起始网址：{self.start_url}")
        log.info(f"下载文件类型：{self.extensions}")
        log.info(f"保存目录：{self.save_dir}")
        log.info("=" * 60)

        self.save_dir.mkdir(parents=True, exist_ok=True)

        self.login()
        self.crawl(self.start_url, depth=0)

        log.info("=" * 60)
        log.info(f"爬取完成。共访问页面 {len(self.visited_pages)} 个，"
                  f"成功下载 {self.download_count} 个文件，失败 {self.fail_count} 个。")
        log.info(f"文件保存在：{self.save_dir}")
        log.info("=" * 60)


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.ini"
    downloader = WebFileDownloader(config_path)
    downloader.run()


if __name__ == "__main__":
    main()