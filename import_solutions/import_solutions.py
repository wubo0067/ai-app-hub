#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import aiohttp
import aiofiles
import yaml
import logging
import logger as logmod


class ImportSolutions:
    def __init__(self, config):
        self.config = config
        datasource = config.get('datasource', {})
        rag_cfg = config.get('rag', {})
        iobs_cfg = config.get('iobs', {})
        logging_cfg = config.get('logging', {})

        self.root_dir = datasource.get('root_dir', './data')
        self.process_file = logging_cfg.get('process_record_file', './.process')

        self.max_concurrency = max(1, int(rag_cfg.get('max_concurrent_files', 10)))  # 同时上传多少个

        timeout_seconds = float(iobs_cfg.get('timeout', 60))
        self.http_timeout = aiohttp.ClientTimeout(total=timeout_seconds)

        # IOBS 配置
        server = (iobs_cfg.get('server') or '').rstrip('/')
        port = iobs_cfg.get('port')
        endpoint = iobs_cfg.get('api_endpoint', '')
        if endpoint and not endpoint.startswith('/'):
            endpoint = f'/{endpoint}'
        base_url = server if not port else f"{server}:{port}"
        self.iobs_url = f"{base_url}{endpoint}" if base_url else endpoint
        if not self.iobs_url:
            logmod.logger.warning("iobs server endpoint is empty; uploads will fail.")

        self.iobs_payload_defaults = {
            'tenantId': iobs_cfg.get('tenant_id'),
            'userName': iobs_cfg.get('user_name'),
        }

        # RAG 配置（如果需要第二步导入）
        rag_server = (rag_cfg.get('server') or '').rstrip('/')
        rag_port = rag_cfg.get('port')
        rag_endpoint = rag_cfg.get('api_endpoint', '')
        if rag_endpoint and not rag_endpoint.startswith('/'):
            rag_endpoint = f'/{rag_endpoint}'
        rag_base_url = rag_server if not rag_port else f"{rag_server}:{rag_port}"
        self.rag_url = f"{rag_base_url}{rag_endpoint}" if rag_base_url else rag_endpoint

        self.rag_payload_defaults = {
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
                        self.imported_count += processed
                        # 每导入一批后，sleep for one second
                        await asyncio.sleep(1)

            logmod.logger.info(f"Import process completed. Success count: {self.imported_count}")
            return True
        except asyncio.CancelledError:
            logmod.logger.warning("Import task cancelled by user (Ctrl+C detected).")
            raise

    async def import_batch_files(self, batch_files, session, semaphore):
        if not batch_files:
            return 0

        rel_paths = [self._relative_path(path) for path in batch_files]
        logmod.logger.info(f"Processing batch files: {rel_paths}")

        self._record_progress(batch_files[0])
        # 使用 asyncio.gather 并发处理每个文件的导入
        tasks = [self.import_file_with_session(session, path, semaphore) for path in batch_files]
        results = await asyncio.gather(*tasks)

        success = sum(1 for ok in results if ok)
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

            # 构建上传 iobs 的请求体
            payload = {
                **{k: v for k, v in self.iobs_payload_defaults.items() if v is not None},
                'file': md_content,
            }

            # 打印完整的 POST 请求信息
            logmod.logger.debug(f"file_path: {file_path}")
            logmod.logger.debug(f"POST Request URL: {self.iobs_url}")
            logmod.logger.debug(f"POST Request Headers: Content-Type=application/json")
            logmod.logger.debug(f"POST Request Payload: {payload}")


            try:
                async with session.post(self.iobs_url, json=payload) as response:
                    if response.content_type == 'application/json':
                        rsp_data = await response.json()
                    else:
                        rsp_data = {'status': response.status, 'body': await response.text()}
                        logmod.logger.error(f"Unexpected response for {rel_path}: {rsp_data}")
                        return False
            except Exception as exc:
                logmod.logger.error(f"Failed to upload file {rel_path} to iobs: {exc}")
                return False

            if rsp_data.get('state') == 'success' and rsp_data.get('code') == 200:
                logmod.logger.info(f"Imported file: {rel_path}")

                #上传 iobs 成功后，将 markdown 导入 RAG 系统
                # 构建上传 RAG 的请求体
                rag_payload = {
                    **{k: v for k, v in self.rag_payload_defaults.items() if v is not None},
                    'docFiles': [{
                        'fileKey' : rsp_data.get('data', {}).get('fileKey'),
                        'fileSize' : str(rsp_data.get('data', {}).get('fileSize')),
                        'docType' : 'MD',
                        'name' : rsp_data.get('data', {}).get('fileName'),
                        'fileUrl' : rsp_data.get('data', {}).get('fileUrl'),
                        }],
                }

                logmod.logger.debug(f"RAG POST Request URL: {self.rag_url}")
                logmod.logger.debug(f"RAG POST Request Headers: Content-Type=application/json")
                logmod.logger.debug(f"RAG POST Request Payload: {rag_payload}")

                try:
                    async with session.post(self.rag_url, json=rag_payload) as rag_response:
                        if rag_response.content_type == 'application/json':
                            rag_rsp_data = await rag_response.json()
                        else:
                            rag_rsp_data = {'status': rag_response.status, 'body': await rag_response.text()}
                            logmod.logger.error(f"Unexpected RAG response for {rel_path}: {rag_rsp_data}")
                            return False
                except Exception as exc:
                    logmod.logger.error(f"Failed to upload file {rel_path} to RAG: {exc}")
                    return False

                if rag_rsp_data.get('state') == 'success':
                    logmod.logger.info(f"RAG import successful for file: {rel_path}, RAG response: {rag_rsp_data}")
                    return True

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