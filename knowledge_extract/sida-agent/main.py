#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初中理科全科知识库 Agent 入口。

流程：
1. extract_pdf_pages_as_markdown：用多模态视觉模型将 PDF
   指定物理页提取为 Markdown；
2. build_knowledge_bases(subject=...)：对页文本提炼全科知识网络
   （概念拆解/公式推导/实验图解/题型溯源），写入向量库 + 图谱库；
   可对 physics/chemistry/math 分别喂入对应教材并共享同一份双库，
   累积成三科知识库；
3. create_circuit_agent：构建全科问答 Agent（自动判定学科并检索）；
4. invoke：对学生提问生成分层讲解并打印。

模型服务（base_url / api_key / model_name）统一在 sida-agent/.env 中配置，
见 config.py。
"""

from __future__ import annotations

from agent.workflow import create_circuit_agent
from ingestion import build_knowledge_bases
from logger import get_logger
from pdf_processor import extract_pdf_pages_as_markdown
from storage.graph_store import ScienceGraphStore
from storage.vector_store import get_vector_store

log = get_logger()


def main() -> None:
    # 1. 配置密钥（优先从 .env / 环境读取，见 config.py）
    # 视觉解析：VISION_BASE_URL / VISION_MODEL / VISION_API_KEY
    # 推理（通用）：REASONING_BASE_URL / REASONING_MODEL / REASONING_API_KEY
    # Embedding：OPENAI_API_KEY / OPENAI_BASE_URL

    # 共享的双库实例：多学科教材可反复累积进同一份知识库
    vector_db = get_vector_store()
    graph_db = ScienceGraphStore()

    # 2. 多模态提取 PDF 物理页码（例如提取第 6 页到第 12 页）
    pdf_file_path = "H:/wechat_files/xwechat_files/calm-wu_9d75/msg/file/2026-08/9S合并PDF.pdf"
    log.info("[main] 流水线启动, PDF=%s", pdf_file_path)
    pages_data = extract_pdf_pages_as_markdown(
        pdf_path=pdf_file_path,
        start_page=11,
        end_page=12,
    )

    # 3. 结构化抽取并构建双库。
    #    每次调用传入学科：physics / chemistry / math。
    #    后续若要加入其它学科/教材，用同一 vector_db/graph_db 再次调用即可累积：
    #    build_knowledge_bases(pages_data=chemistry_pages, subject="chemistry",
    #                          vector_db=vector_db, graph_db=graph_db)
    vector_db, graph_db = build_knowledge_bases(
        pages_data=pages_data,
        subject="physics",
        vector_db=vector_db,
        graph_db=graph_db,
    )

    # 4. 构建并运行全科问答 Agent
    agent = create_circuit_agent(
        vector_db=vector_db,
        graph_db=graph_db,
    )

    # 5. 执行测试提问（可问物理/化学/数学，Agent 会先判定学科再检索）
    test_query = "请帮我系统讲解可变电路的分析思路，并用具体的典型例题带我推导一遍"
    log.info("[main] Agent 正在处理学生提问: %s", test_query)

    try:
        result = agent.invoke({"query": test_query})
    except Exception:
        log.exception("[main] Agent 执行失败")
        raise

    print("=" * 60)
    print("【解答生成结果】:\n")
    print(result["final_answer"])
    print("=" * 60)
    log.info("[main] 流水线完成")


if __name__ == "__main__":
    main()