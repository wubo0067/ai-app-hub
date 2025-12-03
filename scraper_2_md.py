import requests
from bs4 import BeautifulSoup
import markdownify
import os
from datetime import datetime
from urllib.parse import urlparse
import argparse
import sys

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

            # 解析登录表单，查找 CSRF token 或其他隐藏字段（如果需要）
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

            # 检查登录是否成功（可以根据实际情况调整）
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
        # 依次尝试常见的内容容器标签
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
    parser.add_argument('url', help='要爬取的目标页面 URL')
    parser.add_argument('--login-url', default='https://access.redhat.com/login',
                        help='登录页面 URL (默认: https://example.com/login)')
    parser.add_argument('--username', default='admin',
                        help='用户名 (默认: wubo794)')
    parser.add_argument('--password', default='Littlebull0067',
                        help='密码 (默认: password)')
    parser.add_argument('--output', '-o',
                        help='输出文件路径 (默认: 自动生成)')

    args = parser.parse_args()

    # 创建爬虫实例
    scraper = WebScraper(
        login_url=args.login_url,
        username=args.username,
        password=args.password
    )

    # 执行爬取
    success = scraper.run(args.url, args.output)

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())