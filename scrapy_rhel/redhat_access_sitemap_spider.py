import scrapy
from scrapy.spiders import SitemapSpider
from scrapy.crawler import CrawlerProcess
from time import sleep
import re
from scrapy.exporters import JsonItemExporter
import os
import json


class SitemapRoutingPipeline:
    """根据 sitemap URL 路由数据到不同的 JSON 文件"""

    def open_spider(self, spider):
        self.exporters = {}
        self.files = {}
        os.makedirs("./output", exist_ok=True)

    def _get_exporter_for_sitemap(self, sitemap_url):
        """根据 sitemap URL 获取对应的文件句柄"""
        match = re.search(r'(solution-\d+)\.xml', sitemap_url)
        if match:
            filename = f"./output/{match.group(1)}.json"
        else:
            filename = "./output/default.json"

        if filename not in self.files:
            self.files[filename] = open(filename, 'w', encoding='utf-8')
            self.exporters[filename] = []

        return filename

    def process_item(self, item, spider):
        """处理每个 item，写入对应的文件"""
        sitemap_url = item.get('_sitemap_url')
        filename = self._get_exporter_for_sitemap(sitemap_url)

        # 移除内部字段
        clean_item = {k: v for k, v in item.items() if not k.startswith('_')}
        self.exporters[filename].append(clean_item)

        return item

    def close_spider(self, spider):
        """关闭时将数据写入文件"""
        for filename, items in self.exporters.items():
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(items, f, ensure_ascii=False, indent=0)

        for f in self.files.values():
            f.close()


class RedHatLimitedSpider(SitemapSpider):
    name = "redhat_limited_sitemap"
    sitemap_urls = ['https://access.redhat.com/sitemap.xml']

    page_limit = 100000  # 限制实际获取的页面数量
    page_count = 0  # 已获取的页面计数
    parse_count = 0  # parse 函数被调用的次数
    solution_sitemaps = []  # 存储发现的 solution sitemap URL
    current_sitemap = None  # 当前正在处理的 sitemap URL

    def sitemap_filter(self, entries):
        """过滤 sitemap 条目，收集 solution sitemap URL"""
        for entry in entries:
            if 'loc' in entry:
                loc = entry['loc']
                # 发现 solution-*.xml 的 sitemap 文件，保存到列表中
                if 'https://access.redhat.com/webassets/sitemaps/solution/solution-' in loc:
                    self.solution_sitemaps.append(loc)
                    self.logger.info("发现 solution sitemap: %s", loc)
        # 不返回任何内容，阻止 SitemapSpider 继续处理
        return []

    def start_requests(self):
        # 登录请求
        return [scrapy.FormRequest(
            url="https://access.redhat.com/login",
            formdata={
                "username": "wubo794",
                "password": "Littlebull0067"
            },
            callback=self.after_login
        )]

    def after_login(self, response):
        # 登录成功后，读取 solutions.txt 文件中的每一行内容
        self.logger.info("Login successful, starting to read solutions.txt 文件")
        solutions_file = "./solutions.txt"
        try:
            with open(solutions_file, "r") as file:
                for line in file:
                    solution_sitemap = line.strip()
                    if solution_sitemap:  # 确保行不为空
                        self.logger.info("Parse solution sitemap: %s", solution_sitemap)
                        yield scrapy.Request(solution_sitemap, callback=self.parse_solution_sitemap, meta={'sitemap_url': solution_sitemap})
        except FileNotFoundError:
            self.logger.error("未找到 solutions.txt 文件：%s", solutions_file)

    def parse_main_sitemap(self, response):
        """解析主 sitemap，收集 solution-*.xml URL"""
        self.logger.info("正在解析主 sitemap: %s", response.url)
        #print(f"\n=== 正在解析主 sitemap: {response.url} ===")

        # 尝试不同的 XPath 来提取 sitemap URL
        # 方式 1: 使用命名空间
        sitemap_locs = response.xpath('//ns:sitemap/ns:loc/text()',
                                      namespaces={'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}).getall()

        # 方式 2: 不使用命名空间（如果方式 1 为空）
        if not sitemap_locs:
            sitemap_locs = response.xpath('//sitemap/loc/text()').getall()

        # 方式 3: 直接查找所有 loc 元素
        if not sitemap_locs:
            sitemap_locs = response.xpath('//loc/text()').getall()

        print(f"主 sitemap 中找到 {len(sitemap_locs)} 个 URL")

        solution_count = 0
        for loc in sitemap_locs:
            if 'https://access.redhat.com/webassets/sitemaps/solution/solution-' in loc:
                solution_count += 1
                self.logger.info("Found solution sitemap: %s", loc)
                #print(f"发现 solution sitemap ({solution_count}): {loc}")
                # 立即请求并解析这个 sitemap 文件
                yield scrapy.Request(loc, callback=self.parse_solution_sitemap)

        if solution_count == 0:
            self.logger.info("Warning: None found solution sitemap!")

    def parse_solution_sitemap(self, response):
        """解析 solution-*.xml 文件，提取并过滤 loc 元素"""
        sitemap_url = response.meta.get('sitemap_url')

        # 提取所有 loc 元素（使用命名空间）
        locs = response.xpath('//ns:url/ns:loc/text()',
                     namespaces={'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}).getall()
        self.logger.info("Extracted %d URLs from %s", len(locs), response.url)

        # 打印前几个 URL 用于调试
        #if locs:
        #    print(f"前 3 个 URL 示例:")
        #    for i, url in enumerate(locs[:3], 1):
        #        print(f"  {i}. {url}")

        # 过滤并处理符合条件的 URL
        for idx, loc in enumerate(locs, 1):
            # 过滤：只处理以 https://access.redhat.com/solutions/ 开头的 URL
            if loc.startswith('https://access.redhat.com/solutions/'):
                if self.page_count < self.page_limit:
                    self.page_count += 1
                    if self.page_count % 30 == 0:
                        self.logger.info("Crawled %d pages from %s, paused for 3 seconds", self.page_count, response.url)
                        sleep(3)
                    # 生成请求去获取页面标题，传递正确的 sitemap_url
                    yield scrapy.Request(loc, callback=self.parse, meta={'sitemap_url': sitemap_url})
                else:
                    self.logger.info("Page retrieval limit reached: %d", self.page_limit)
                    return  # 直接返回，不再处理更多 sitemap

    def parse(self, response, rate_limit=20):
        self.parse_count += 1
        title = response.css("title::text").get()
        self.logger.info("Crawl page: %s", response.url)
        self.logger.info("Page title: %s", title)

        sitemap_url = response.meta.get('sitemap_url')
        yield {
            "url": response.url,
            "title": title,
            "_sitemap_url": sitemap_url,  # 添加 sitemap URL 用于路由
        }

        # 限速逻辑：每解析 rate_limit 个页面，暂停 2 秒
        if self.parse_count % rate_limit == 0:
            self.logger.info("Parsed %d pages, pause for 2 seconds", self.parse_count)
            sleep(2)


def main():
    process = CrawlerProcess(settings={
        "LOG_LEVEL": "INFO",
        "ITEM_PIPELINES": {
            'redhat_access_sitemap_spider.SitemapRoutingPipeline': 300,
        }
    })
    process.crawl(RedHatLimitedSpider)
    process.start()


if __name__ == "__main__":
    main()
