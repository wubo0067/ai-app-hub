import requests
from bs4 import BeautifulSoup
import markdownify
import os
from datetime import datetime
from urllib.parse import urlparse
import argparse
import sys
import json
from pathlib import Path
from cryptography.fernet import Fernet
import getpass

class CredentialsManager:
    """凭证管理器 - 处理账号密码的加密存储和读取"""

    def __init__(self, config_dir=None):
        """
        初始化凭证管理器

        Args:
            config_dir: 配置文件目录
        """
        if config_dir is None:
            config_dir = os.path.expanduser('~/.web_scraper')

        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.key_file = self.config_dir / 'key.key'
        self.creds_file = self.config_dir / 'credentials.json'

        # 初始化加密密钥
        self._init_key()

    def _init_key(self):
        """初始化或加载加密密钥"""
        if self.key_file.exists():
            # 加载现有密钥
            with open(self.key_file, 'rb') as f:
                self.cipher_suite = Fernet(f.read())
        else:
            # 生成新密钥
            key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
                f.write(key)
            self.cipher_suite = Fernet(key)
            print(f"✓ 已生成新的加密密钥: {self.key_file}")
            print("  请妥善保管此密钥，不要泄露！\n")

    def _encrypt(self, text):
        """加密文本"""
        return self.cipher_suite.encrypt(text.encode()).decode()

    def _decrypt(self, encrypted_text):
        """解密文本"""
        return self.cipher_suite.decrypt(encrypted_text.encode()).decode()

    def save_credentials(self, site_name, username, password):
        """
        保存加密后的账号密码

        Args:
            site_name: 网站名称/标识
            username: 用户名
            password: 密码
        """
        # 加载现有凭证
        credentials = self._load_creds_file()

        # 保存新凭证
        credentials[site_name] = {
            'username': self._encrypt(username),
            'password': self._encrypt(password),
            'saved_at': datetime.now().isoformat()
        }

        # 写入文件
        with open(self.creds_file, 'w') as f:
            json.dump(credentials, f, indent=2)

        print(f"✓ 凭证已保存: {site_name}")

    def get_credentials(self, site_name):
        """
        获取解密后的账号密码

        Args:
            site_name: 网站名称/标识

        Returns:
            (username, password) 元组或 None
        """
        credentials = self._load_creds_file()

        if site_name not in credentials:
            return None

        cred = credentials[site_name]
        return (
            self._decrypt(cred['username']),
            self._decrypt(cred['password'])
        )

    def _load_creds_file(self):
        """加载凭证文件"""
        if self.creds_file.exists():
            with open(self.creds_file, 'r') as f:
                return json.load(f)
        return {}

    def list_credentials(self):
        """列出所有保存的凭证"""
        credentials = self._load_creds_file()
        if not credentials:
            print("没有保存任何凭证")
            return

        print("\n已保存的凭证:")
        for site_name, cred in credentials.items():
            saved_at = cred.get('saved_at', '未知')
            print(f"  - {site_name} (保存于: {saved_at})")

    def delete_credentials(self, site_name):
        """删除保存的凭证"""
        credentials = self._load_creds_file()
        if site_name in credentials:
            del credentials[site_name]
            with open(self.creds_file, 'w') as f:
                json.dump(credentials, f, indent=2)
            print(f"✓ 凭证已删除: {site_name}")
        else:
            print(f"✗ 凭证不存在: {site_name}")


class WebScraper:
    def __init__(self, login_url, username, password):
        """
        初始化爬虫，建立带有认证的会话

        Args:
            login_url: 登录页面 URL
            username: 用户名
            password: 密码
        """
        self.session = requests.Session()
        self.login_url = login_url
        self.username = username
        self.password = password
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

    def login(self):
        """登录到网站"""
        try:
            print(f"正在登录: {self.login_url}")

            # 先访问登录页面获取必要的 cookies 和 tokens
            response = self.session.get(self.login_url, headers=self.headers, timeout=10)
            response.raise_for_status()

            # 解析登录表单，查找必要的隐藏字段
            soup = BeautifulSoup(response.content, 'html.parser')

            # 构建登录数据
            login_data = {
                'username': self.username,
                'password': self.password,
            }

            # 尝试查找隐藏的 token 字段（根据实际网站调整）
            token_field = soup.find('input', {'name': 'csrf_token'})
            if token_field:
                login_data['csrf_token'] = token_field.get('value')

            # 发送登录请求
            login_response = self.session.post(
                self.login_url,
                data=login_data,
                headers=self.headers,
                timeout=10
            )
            login_response.raise_for_status()

            # 检查登录是否成功
            if 'logout' in login_response.text.lower() or login_response.status_code == 200:
                print("✓ 登录成功")
                return True
            else:
                print("✗ 登录可能失败，请检查用户名和密码")
                return False

        except requests.exceptions.RequestException as e:
            print(f"✗ 登录失败: {e}")
            return False

    def scrape_page(self, target_url):
        """
        爬取指定页面

        Args:
            target_url: 目标页面 URL

        Returns:
            BeautifulSoup 对象或 None
        """
        try:
            print(f"正在爬取页面: {target_url}")
            response = self.session.get(target_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            response.encoding = 'utf-8'

            print("✓ 页面爬取成功")
            return BeautifulSoup(response.content, 'html.parser')

        except requests.exceptions.RequestException as e:
            print(f"✗ 爬取失败: {e}")
            return None

    def extract_content(self, soup):
        """
        提取页面主要内容

        Args:
            soup: BeautifulSoup 对象

        Returns:
            提取的内容 (标题, 正文)
        """
        # 提取标题
        title = "未命名文档"
        if soup.title:
            title = soup.title.string
        elif soup.find('h1'):
            title = soup.find('h1').get_text(strip=True)

        # 提取主要内容
        content = None
        for tag in ['article', 'main', 'div[class*="content"]', 'div[class*="article"]']:
            if tag.startswith('div'):
                content = soup.find('div', class_=tag.split('[class*="')[1].rstrip('"]'))
            else:
                content = soup.find(tag)
            if content:
                break

        # 如果没找到，使用整个 body
        if not content:
            content = soup.body if soup.body else soup

        return title, content

    def generate_markdown(self, title, content, target_url):
        """
        将内容转换为 Markdown

        Args:
            title: 文档标题
            content: BeautifulSoup 内容对象
            target_url: 源 URL

        Returns:
            Markdown 字符串
        """
        # 移除脚本和样式标签
        for tag in content.find_all(['script', 'style']):
            tag.decompose()

        # 转换为 Markdown
        markdown_content = markdownify.markdownify(str(content))

        # 生成完整的 Markdown 文档
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        markdown = f"""---
title: {title}
source_url: {target_url}
created_at: {timestamp}
---

# {title}

> 源链接: [{target_url}]({target_url})

---

{markdown_content}
"""
        return markdown

    def save_markdown(self, markdown, output_file=None):
        """
        保存 Markdown 文件

        Args:
            markdown: Markdown 内容
            output_file: 输出文件路径（如果为 None 则自动生成）

        Returns:
            保存的文件路径
        """
        if not output_file:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = f"scraped_content_{timestamp}.md"

        # 创建输出目录
        os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else '.', exist_ok=True)

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(markdown)

        print(f"✓ Markdown 文件已保存: {output_file}")
        return output_file

    def run(self, target_url, output_file=None):
        """
        执行完整的爬取流程

        Args:
            target_url: 目标页面 URL
            output_file: 输出文件路径
        """
        # 登录
        if not self.login():
            print("登录失败，程序终止")
            return False

        # 爬取页面
        soup = self.scrape_page(target_url)
        if not soup:
            print("爬取页面失败，程序终止")
            return False

        # 提取内容
        title, content = self.extract_content(soup)
        print(f"✓ 标题: {title}")

        # 生成 Markdown
        markdown = self.generate_markdown(title, content, target_url)

        # 保存文件
        saved_file = self.save_markdown(markdown, output_file)

        print("\n✓ 所有操作完成！")
        return True


def main():
    parser = argparse.ArgumentParser(description='网页爬取工具 - 登录后爬取页面生成 Markdown')

    subparsers = parser.add_subparsers(dest='command', help='命令')

    # 爬取命令
    scrape_parser = subparsers.add_parser('scrape', help='爬取页面')
    scrape_parser.add_argument('url', help='要爬取的目标页面 URL')
    scrape_parser.add_argument('--login-url', required=True, help='登录页面 URL')
    scrape_parser.add_argument('--site-name', required=True, help='网站标识（用于管理凭证）')
    scrape_parser.add_argument('--output', '-o', help='输出文件路径')

    # 保存凭证命令
    save_parser = subparsers.add_parser('save-creds', help='保存账号密码（加密）')
    save_parser.add_argument('--site-name', required=True, help='网站标识')

    # 列出凭证命令
    list_parser = subparsers.add_parser('list-creds', help='列出所有保存的凭证')

    # 删除凭证命令
    delete_parser = subparsers.add_parser('delete-creds', help='删除保存的凭证')
    delete_parser.add_argument('--site-name', required=True, help='网站标识')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # 初始化凭证管理器
    creds_manager = CredentialsManager()

    if args.command == 'save-creds':
        # 保存凭证
        username = input("请输入用户名: ")
        password = getpass.getpass("请输入密码: ")
        creds_manager.save_credentials(args.site_name, username, password)
        return 0

    elif args.command == 'list-creds':
        # 列出凭证
        creds_manager.list_credentials()
        return 0

    elif args.command == 'delete-creds':
        # 删除凭证
        creds_manager.delete_credentials(args.site_name)
        return 0

    elif args.command == 'scrape':
        # 爬取页面
        creds = creds_manager.get_credentials(args.site_name)
        if not creds:
            print(f"✗ 未找到凭证: {args.site_name}")
            print("请先使用以下命令保存凭证:")
            print(f"  python scraper.py save-creds --site-name {args.site_name}")
            return 1

        username, password = creds

        # 创建爬虫实例
        scraper = WebScraper(
            login_url=args.login_url,
            username=username,
            password=password
        )

        # 执行爬取
        success = scraper.run(args.url, args.output)
        return 0 if success else 1

    return 0


if __name__ == '__main__':
    sys.exit(main())