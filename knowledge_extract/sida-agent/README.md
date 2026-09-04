# sida-agent · 初中理科全科知识库问答 Agent

把初中**物理 / 化学 / 数学**教材与讲义 PDF 转化为可溯源的知识库，并对学生提问生成
「概念拆解 → 公式推导 → 实验图解 → 题型溯源 → 例题带练」的分层讲解。

## 1. 这个项目是做什么的

一条端到端流水线（入口 `main.py`）：

```
PDF 讲义 ──① 视觉大模型提取──▶ 结构化 Markdown（逐页缓存，断点续跑）
        ──② LLM 结构化抽取（两批串行 + 磁盘缓存）──▶ 双知识库
              ├─ 知识图谱 ScienceGraphStore（NetworkX 内存图）
              │    节点键 {subject}:{Kind}:{name}，三科命名空间隔离
              │    概念/公式/实验/题型/方法/例题 + 前置/溯源/示范等关系
              └─ 向量库 Chroma（metadata.id 与图节点键/讲义页键一致，供精确回表）
                   实体切片 + 讲义页切片 subject:Page:页码
        ──③ LangGraph 问答 Agent──▶ 分层讲解
              判定学科与知识点锚点 → 图谱聚合检索（模糊解析锚点）
              → 按例题页码回表取讲义页原文 → 生成回答
```

抽取本体（跨学科通用 schema，见 `ingestion.py`）：章节、概念（拆解/易错/前置）、
公式（符号表/适用条件/推导步骤）、实验（器材/步骤/现象/结论/装置图解）、
题型（识别特征/解题模板/陷阱）、例题（编号/小标题/归属题型/结构化出处含页码，
原文不由 LLM 抄写，问答时按 `source.page` 回表取讲义页原文）、通法技巧。

同一对 `vector_db` / `graph_db` 可被多个学科反复调用 `build_knowledge_bases`
累积灌入，形成三科合一的知识库。

抽取提速：LLM 抽取拆为**两批串行**（第一批知识体系 → 第二批题型与例题，并注入
第一批的概念名保证引用一致），且关闭思考模式（`enable_thinking=False`）；抽取
结果按「学科 + schema 版本 + 输入全文」哈希缓存于 `output/extract_cache/`，
相同输入重跑 **0 次 LLM 调用**。

## 2. 常用命令

环境要求：Python ≥ 3.11，[uv](https://docs.astral.sh/uv/)。

### 安装

```powershell
uv sync                 # 按 pyproject.toml + uv.lock 安装依赖到 .venv
```

### 配置

复制/编辑项目根 `.env`（已被 `.gitignore` 忽略，勿提交）：

| 变量 | 用途 |
|---|---|
| `VISION_BASE_URL` / `VISION_MODEL` / `VISION_API_KEY` | 视觉解析模型（PDF 页 → Markdown） |
| `REASONING_BASE_URL` / `REASONING_MODEL` / `REASONING_API_KEY` | 推理模型（知识抽取〔关闭思考模式〕+ 问答） |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | Embedding（向量库） |

`config.py` 启动时加载 `.env`（系统同名环境变量优先）。业务代码不指定模型：
视觉解析用 `config.get_vision_llm()`，抽取/问答用 `config.get_reasoning_llm()`，
由 `.env` 固定各自使用哪个大模型。

### 启动

```powershell
# 把教材 PDF 放到项目根目录，并按需修改 main.py 中的路径/页码/学科
uv run python main.py
```

### 测试 / 自检

项目暂无正式测试套件，常用冒烟方式：

```powershell
# 全模块导入冒烟
uv run python -c "import main, ingestion, pdf_processor, config, agent.workflow, storage.graph_store, storage.vector_store; print('ALL IMPORTS OK')"

# 查看运行日志（控制台 INFO，文件 DEBUG）
Get-Content output\sida_agent.log -Tail 50
```

## 3. 重要目录与文件

| 路径 | 重要度 | 说明 |
|---|---|---|
| `main.py` | ★★★ | 流水线入口：提取 → 建库 → 问答，演示完整调用方式 |
| `config.py` | ★★★ | 统一 LLM/Embedding 工厂；`.env` 中 base_url/key/model 在此生效 |
| `ingestion.py` | ★★★ | 核心：两批串行抽取 prompt、抽取磁盘缓存、学科引导（SUBJECT_META）、双库写入编排 |
| `agent/workflow.py` | ★★★ | LangGraph 问答工作流：学科判定 → 图谱检索 → 按页码回表讲义页 → 生成 |
| `storage/graph_store.py` | ★★★ | `ScienceGraphStore` 图谱存储、`get_subgraph` 聚合检索、概念锚点模糊解析 |
| `pdf_processor.py` | ★★ | PDF 页渲染 + 视觉模型提取 Markdown，逐页缓存于 `output/pdf_extract/` |
| `storage/vector_store.py` | ★★ | Chroma 向量库初始化（collection `science_kb`） |
| `agent/state.py` | ★ | Agent 状态 TypedDict（query / target_subject / 检索结果等） |
| `logger.py` | ★ | 控制台 + `output/sida_agent.log` 双通道日志 |
| `.env` | ★★ | 模型服务配置（不入库）；`.env` 缺失或 key 为空时启动会给出指引报错 |
| `output/` | — | 运行产物：日志、PDF 提取缓存（`pdf_extract/{pdf_id}/pXXXX.md`）、抽取缓存（`extract_cache/{hash}.json`） |

## 4. 其它说明

- PDF 提取缓存目录结构 `output/pdf_extract/{pdf_id}/p{页码}.md`，`pdf_id` 为 PDF
  内容 SHA-256 前 16 位，与姊妹项目 `knowledge_extract/extract_pdf` 同算法，可互相复用缓存。
- 图谱为内存版（进程退出即消失），向量库为 Chroma 默认本地持久化；重启后需重跑
  `build_knowledge_bases`（PDF 提取缓存命中则不调视觉模型，抽取缓存命中则不调推理
  LLM，重建仅剩向量/图写入，秒级完成）。
- 向量写入以 `metadata.id` 为键幂等 upsert，重复重建不会在 Chroma 中累积重复切片；
  修改抽取 schema 后请递增 `ingestion._EXTRACT_SCHEMA_VERSION` 使旧抽取缓存失效。
