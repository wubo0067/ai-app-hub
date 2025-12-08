#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import aiohttp
import aiofiles
import yaml # pip3 install pyyaml
import logging
import logger as logmod


class ImportSolutions:
    def __init__(self, config):
        self.config = config
        datasource = config.get('datasource', {})
        rag_cfg = config.get('rag', {})
        logging_cfg = config.get('logging', {})

        env = rag_cfg.get('env', 'prod').lower()
        rag_server = ''  # 添加默认值
        if env == 'prod':
            rag_server = rag_cfg.get('prod_url', '')
        elif env == 'stg':
            rag_server = rag_cfg.get('stg_url', '')
        else:
            # 添加 else 分支处理其他情况
            logmod.logger.warning(f"Unknown environment: {env}, using prod as default")
            rag_server = rag_cfg.get('prod_url', '')

        self.root_dir = datasource.get('root_dir', './data')
        self.process_file = logging_cfg.get('process_record_file', './.process')
        self.max_concurrency = max(1, int(rag_cfg.get('max_concurrent_files', 10)))  # 同时上传多少个
        self.tenant_id = rag_cfg.get('tenant_id')
        self.token = None

        timeout_seconds = float(rag_cfg.get('timeout', 10))
        self.http_timeout = aiohttp.ClientTimeout(total=timeout_seconds)

        port = rag_cfg.get('port')
        base_url = rag_server if not port else f"{rag_server}:{port}"

        # login endpoint
        endpoint = rag_cfg.get('login_api_endpoint', '')
        if endpoint and not endpoint.startswith('/'):
            endpoint = f'/{endpoint}'
        self.login_url = f"{base_url}{endpoint}" if base_url else endpoint
        if not self.login_url:
            logmod.logger.fatal("RAG login API endpoint is empty; authentication may fail.")

        self.login_payload_default = {
            'appId': self.tenant_id,
            'appSecret': rag_cfg.get('secret_key'),
        }

        # RAG 文件上传
        endpoint = rag_cfg.get('upload_api_endpoint', '')
        if endpoint and not endpoint.startswith('/'):
            endpoint = f'/{endpoint}'
        self.upload_url = f"{base_url}{endpoint}" if base_url else endpoint
        if not self.upload_url:
            logmod.logger.fatal("RAG upload API endpoint is empty; uploads will fail.")

        self.upload_payload_defaults = {
            'tenantId': rag_cfg.get('tenant_id'),
            'userName': rag_cfg.get('user_name'),
        }

        # RAG 导入
        endpoint = rag_cfg.get('import_api_endpoint', '')
        if endpoint and not endpoint.startswith('/'):
            endpoint = f'/{endpoint}'
        self.import_url = f"{base_url}{endpoint}" if base_url else endpoint
        if not self.import_url:
            logmod.logger.fatal("RAG import API endpoint is empty; imports will fail.")

        self.import_payload_defaults = {
            'knId': rag_cfg.get('knowledge_base_id'),
            'categoryId': rag_cfg.get('category_id'),
            'source': rag_cfg.get('source'),
            'tenantId': rag_cfg.get('tenant_id'),
            'userName': rag_cfg.get('user_name'),
            'isKnEnhance': 0,
            'segType': "AUTO",
        }

        self.imported_count = 0
        self.failed_files = []

    # 开始导入
    async def start(self):
        try:
            last_record = self._load_last_record()
            if last_record:
                logmod.logger.info(f"Resuming from last record: {last_record}")
            else:
                logmod.logger.info("No previous process record found, starting fresh.")

            if not os.path.exists(self.root_dir):
                logmod.logger.error(f"Root directory does not exist: {self.root_dir}")
                return False

            # 异步上下文
            # 创建一个 HTTP 客户端会话对象，设置超时时间，session：获取到的会话实例，用于后续 HTTP 请求
            async with aiohttp.ClientSession(timeout=self.http_timeout) as session:
                # 登录获取 token
                if not await self.login(session):
                    logmod.logger.error("Login failed, aborting import.")
                    return False

                semaphore = asyncio.Semaphore(self.max_concurrency)

                for entry in os.scandir(self.root_dir):
                    if not entry.is_dir():
                        # 如果不是目录，则跳过
                        continue
                    # 如果是目录，则处理该目录下的所有 Markdown 文件
                    subdir_path = entry.path
                    logmod.logger.info(f"Processing subdirectory: {subdir_path}")

                    files = [
                        os.path.join(subdir_path, name)
                        for name in os.listdir(subdir_path)
                        if name.lower().endswith('.md')
                    ]
                    if not files:
                        logmod.logger.info(f"No markdown files found in {subdir_path}")
                        continue

                    # 批量获取文件列表
                    for i in range(0, len(files), self.max_concurrency):
                        batch_files = files[i:i + self.max_concurrency]
                        batch_tokens = [self._relative_path(path) for path in batch_files]

                        if last_record:
                            if last_record not in batch_tokens:
                                continue
                            logmod.logger.info(f"Found last record '{last_record}', resuming import.")
                            last_record = None

                        processed = await self.import_batch_files(batch_files, session, semaphore)
                        # 如果 processed 为 0，说明该批次全部失败，程序终止导入，退出
                        if processed == 0:
                            logmod.logger.error("All files in the current batch failed to import, aborting further imports.")
                            return False
                        self.imported_count += processed
                        # 每导入一批后，sleep for one second
                        await asyncio.sleep(1)

            logmod.logger.info(f"Import process completed. Success count: {self.imported_count}")
            return True
        except asyncio.CancelledError:
            logmod.logger.warning("Import task cancelled by user (Ctrl+C detected).")
            raise

    async def login(self, session):
        try:
            headers = {'Content-Type': 'application/json'}
            async with session.post(self.login_url, json=self.login_payload_default, headers=headers) as response:
                if response.status == 200:
                    #解析响应 JSON 数据
                    rsp_data = await response.json()
                    if rsp_data.get('code') == 0:
                        # 登录成功，获取 token
                        token = rsp_data.get('data')
                        self.token = token
                        logmod.logger.info(f"Login successful. tenant_id:{self.tenant_id} Token: {token}")
                        return True
                    else:
                        logmod.logger.error(f"Login failed: server returned code {rsp_data.get('code')}, message: {rsp_data.get('message', 'N/A')}")
                        return False
                else:
                    logmod.logger.error(f"Login failed with status code: {response.status}")
                    return False
        except aiohttp.ClientError as e:
            logmod.logger.error(f"Login request failed: {e}")
            return False

    async def import_batch_files(self, batch_files, session, semaphore):
        if not batch_files:
            return 0

        rel_paths = [self._relative_path(path) for path in batch_files]
        logmod.logger.info(f"Processing batch files: {rel_paths}")

        # 使用 asyncio.gather 并发处理每个文件的导入
        tasks = [self.import_file_with_session(session, path, semaphore) for path in batch_files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = sum(1 for ok in results if ok is True)
        failures = len(results) - success
        if failures:
            logmod.logger.warning(f"Batch completed with {failures} failures.")
        return success

    async def import_file_with_session(self, session, file_path, semaphore):
        rel_path = self._relative_path(file_path)
        async with semaphore:
            try:
                # 异步读取 markdown 文件
                async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                    md_content = await f.read()
            except FileNotFoundError:
                logmod.logger.error(f"File not found: {file_path}")
                return False
            except Exception as exc:
                logmod.logger.error(f"Failed to read {file_path}: {exc}")
                return False

            headers = {
                    'auth-token': self.token}

            # 构建上传 iobs 的请求体 - 使用 FormData
            form_data = aiohttp.FormData()
            # 添加文件
            form_data.add_field('file',
                                md_content,
                                filename=os.path.basename(file_path),
                                content_type='text/markdown')
            # 添加其他参数
            for k, v in self.upload_payload_defaults.items():
                if v is not None:
                    form_data.add_field(k, str(v))

            # 打印完整的 POST 请求信息
            logmod.logger.debug(f"Upload file_path: {file_path}")
            #logmod.logger.debug(f"POST Request URL: {self.upload_url}")
            #logmod.logger.debug(f"POST Request Headers: auth-token")
            #logmod.logger.debug(f"POST Request Payload: multipart/form-data")


            try:
                # 文件上传需要使用 multipart/form-data 编码
                async with session.post(self.upload_url, data=form_data, headers=headers) as response:
                    if response.content_type == 'application/json':
                        rsp_data = await response.json()
                        logmod.logger.debug(f"Upload response: {rsp_data}")
                    else:
                        rsp_data = {'status': response.status, 'body': await response.text()}
                        logmod.logger.error(f"Unexpected response for {rel_path}: {rsp_data}")
                        return False
            except Exception as exc:
                logmod.logger.error(f"Failed to upload file {rel_path} to upload URL: {exc}")
                return False

            if rsp_data.get('state') == 'success' and rsp_data.get('code') == 200:
                logmod.logger.info(f"Imported file: {rel_path}")

                #上传 iobs 成功后，将 markdown 导入 RAG 系统
                # 构建上传 RAG 的请求体
                payload = {
                    **{k: v for k, v in self.import_payload_defaults.items() if v is not None},
                    'docFiles': [{
                        'fileKey' : rsp_data.get('data', {}).get('fileKey'),
                        'fileSize' : str(rsp_data.get('data', {}).get('fileSize')),
                        'docType' : 'MD',
                        'name' : rsp_data.get('data', {}).get('fileName'),
                        'fileUrl' : rsp_data.get('data', {}).get('fileUrl'),
                        }],
                }

                logmod.logger.debug(f"RAG POST Request URL: {self.import_url}")
                #logmod.logger.debug(f"RAG POST Request Headers: Content-Type=application/json")
                #logmod.logger.debug(f"RAG POST Request Payload: {payload}")

                try:
                    async with session.post(self.import_url, json=payload, headers=headers) as rag_response:
                        if rag_response.content_type == 'application/json':
                            rag_rsp_data = await rag_response.json()
                        else:
                            rag_rsp_data = {'status': rag_response.status, 'body': await rag_response.text()}
                            logmod.logger.error(f"Unexpected RAG response for {rel_path}: {rag_rsp_data}")
                            return False
                except Exception as exc:
                    logmod.logger.error(f"Failed to upload file {rel_path} to import URL: {exc}")
                    return False

                if rag_rsp_data.get('state') == 'success':
                    logmod.logger.info(f"RAG import successful for file: {rel_path}, RAG response: {rag_rsp_data}")
                    # 只在导入成功后才记录进度
                    self._record_progress(file_path)
                    return True
                else:
                    logmod.logger.error(f"RAG import failed for {rel_path}: {rag_rsp_data}")
            else:
                logmod.logger.error(f"File upload failed for {rel_path}: {rsp_data}")

            return False

    def _load_last_record(self):
        try:
            with open(self.process_file, 'r', encoding='utf-8') as f:
                line = f.readline().strip()
                return line or None
        except FileNotFoundError:
            return None
        except Exception as exc:
            logmod.logger.warning(f"Unable to read process record '{self.process_file}': {exc}")
            return None

    def _record_progress(self, file_path):
        record = self._relative_path(file_path)
        record_dir = os.path.dirname(self.process_file)
        if record_dir:
            os.makedirs(record_dir, exist_ok=True)
        with open(self.process_file, 'w', encoding='utf-8') as f:
            f.write(record + '\n')

    def _relative_path(self, absolute_path):
        try:
            return os.path.relpath(absolute_path, self.root_dir)
        except ValueError:
            return absolute_path


# 读取配置文件 config.yaml
def read_config(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
    except FileNotFoundError:
        print(f"[!] Configuration file '{file_path}' does not exist")
        return None
    except yaml.YAMLError as e:
        print(f"[!] Error parsing configuration file: {e}")
        return None
    return dict(config)

if __name__ == "__main__":
    config = read_config("./config.yaml")
    if config is None:
        exit(1)

    logmod.setup_logger(config)
    logmod.logger.info("Import solutions started")

    instance = ImportSolutions(config)
    try:
        asyncio.run(instance.start())
    except KeyboardInterrupt:
        logmod.logger.warning("Ctrl+C received, stopping asynchronous import...")
    finally:
        logging.shutdown()