#!/usr/bin/env python3
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

        self.config = configparser.ConfigParser()
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
        - 【问题3】多个 type="password" 时不再简单"后者覆盖前者"，
          优先选择字段名不含"确认/再次/confirm/repeat"等含义的那一个。
        - 【问题2】用户名候选字段加入黑名单过滤（验证码/短信验证码等），
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
        """尝试登录网站，保持会话 Cookie"""
        if not self.enable_login:
            log.info("未启用登录，跳过登录步骤。")
            return

        log.info(f"正在访问登录页面：{self.login_url}")
        try:
            resp = self.session.get(self.login_url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error(f"访问登录页面失败：{e}")
            sys.exit(1)

        # 自动尝试从登录页面表单中提取隐藏字段（如 csrf_token）
        form_data = dict(self.extra_fields)
        soup = BeautifulSoup(resp.text, "lxml")
        # 优先选择包含 password 输入框的登录表单
        forms=soup.find_all("form")
        form=None
        for f in forms:
            if f.find("input",{"type":"password"}):
                form=f
                break
        if form is None and forms:
            form=forms[0]
        if form is None:
            debug_path = self._save_debug_html(resp.text, "login_page_debug.html")
            raise RuntimeError(
                "登录页面中未找到任何 <form> 标签，无法自动识别登录字段。"
                f"已将抓取到的原始 HTML 保存到 {debug_path}，请打开确认："
                "1) 是否包含 <form>/<input> 标签（若没有，大概率是页面由 JavaScript 动态渲染，"
                "本工具基于 requests+BeautifulSoup 无法处理，需要改用 Selenium/Playwright 等"
                "浏览器自动化方案，或直接抓包分析真实登录接口后在 config.ini 的 extra_fields "
                "中手动指定登录参数）；2) 登录表单是否位于 iframe 中。"
            )
        self.username_field,self.password_field=self._detect_login_fields(form)
        action=self._attr_str(form.get("action"))
        if action:
            self.login_post_url=urljoin(self.login_url,action)
        for inp in form.find_all("input"):
            name=inp.get("name")
            if not name or name in (self.username_field,self.password_field): continue
            if name in form_data: continue
            form_data[name]=inp.get("value","")
        if not self.username_field or not self.password_field:
            debug_path = self._save_debug_html(resp.text, "login_page_debug.html")
            raise RuntimeError(
                f"无法自动识别登录表单中的用户名或密码字段"
                f"（用户名字段：{self.username_field!r}，密码字段：{self.password_field!r}）。"
                f"已将原始 HTML 保存到 {debug_path} 供人工核对，也可在 config.ini 的 "
                f"[login] extra_fields 中手动补充/覆盖字段名。"
            )
        log.info(f"已识别登录字段 -> 用户名字段: '{self.username_field}'，"
                 f"密码字段: '{self.password_field}'，提交地址: {self.login_post_url}")
        form_data[self.username_field]=self.username
        form_data[self.password_field]=self.password

        log.info(f"正在提交登录表单到：{self.login_post_url}")
        try:
            login_resp = self.session.post(
                self.login_post_url, data=form_data, timeout=self.timeout, allow_redirects=True
            )
            login_resp.raise_for_status()
        except requests.RequestException as e:
            log.error(f"登录请求失败：{e}")
            sys.exit(1)

        log.info(f"登录请求已完成（状态码 {login_resp.status_code}，最终地址 {login_resp.url}）。")
        self._verify_login(login_resp)
    @staticmethod
    def _save_debug_html(html_text: str, filename: str) -> str:
        try:
            debug_path = Path(filename).resolve()
            debug_path.write_text(html_text, encoding="utf-8", errors="ignore")
            return str(debug_path)
        except OSError as e:
            log.error(f"保存调试 HTML 失败：{e}")
            return "(保存失败)"
    def _verify_login(self, login_resp: "requests.Response") -> None:
        """启发式判断登录是否成功；不保证 100% 准确，仅供参考。"""
        fail_keywords = [
            "用户名或密码错误", "账号或密码错误", "密码错误", "账号不存在",
            "登录失败", "验证码错误", "invalid username", "invalid password",
            "incorrect password", "login failed", "authentication failed",
            "wrong password",
        ]
        text_lower = login_resp.text[:5000].lower()
        matched = next((kw for kw in fail_keywords if kw.lower() in text_lower), None)
        if matched:
            log.warning(f"登录响应中检测到疑似失败关键词：'{matched}'，"
                        f"请人工确认账号密码是否正确（该判断为启发式，也可能是误判）。")
        elif self.login_url.rstrip("/") == login_resp.url.rstrip("/"):
            log.warning("登录提交后仍停留在登录页地址，可能登录未成功，请人工确认。")
        else:
            log.info("登录响应未检测到明显失败迹象（启发式判断，不代表 100% 成功）。")
        if not self.verify_url:
            log.info("未配置 [login] verify_url，跳过二次登录状态校验。"
                     "如需更可靠的确认，建议配置 verify_url + verify_success_text。")
            return
        log.info(f"正在访问校验页面确认登录状态：{self.verify_url}")
        try:
            verify_resp = self.session.get(self.verify_url, timeout=self.timeout)
        except requests.RequestException as e:
            log.warning(f"访问校验页面失败，跳过二次校验：{e}")
            return
        body = verify_resp.text
        if self.verify_fail_text and self.verify_fail_text in body:
            log.warning(f"校验页面中出现了 verify_fail_text（'{self.verify_fail_text}'），"
                        f"登录很可能未成功。")
        elif self.verify_success_text:
            if self.verify_success_text in body:
                log.info(f"校验页面中出现了 verify_success_text（'{self.verify_success_text}'），登录确认成功。")
            else:
                log.warning(f"校验页面中未出现 verify_success_text（'{self.verify_success_text}'），"
                            f"登录可能未成功，请人工确认。")
        else:
            log.info(f"已访问校验页面（状态码 {verify_resp.status_code}），"
                     f"但未配置 verify_success_text/verify_fail_text，无法自动判断，请人工查看。")

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