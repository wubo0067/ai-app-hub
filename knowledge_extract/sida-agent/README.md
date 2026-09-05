# sida-agent · 初中理科全科知识库问答 Agent

把初中**物理 / 化学 / 数学**教材与讲义 PDF 转化为可溯源的知识库，并对学生提问生成
「概念拆解 → 公式推导 → 实验图解 → 题型溯源 → 例题带练」的分层讲解。

## 1. 这个项目是做什么的

一条端到端流水线（入口 `main.py`）：

```
PDF 讲义 ──① 视觉大模型提取──▶ 结构化 Markdown（逐页缓存，断点续跑）
        ──② LLM 结构化抽取（自动分块增量 + 两批串行 + 滚动上下文 + 磁盘缓存）──▶ 双知识库
              ├─ 知识图谱 ScienceGraphStore（NetworkX 内存图）
              │    节点键 {subject}:{Kind}:{name}，三科命名空间隔离
              │    概念/公式/实验/题型/方法/例题 + 前置/溯源/示范等关系
              └─ 向量库 Chroma（metadata.id 与图节点键/讲义页键一致，供精确回表）
                   实体切片 + 讲义页切片 subject:Page:{pdf_id}:页码
        ──③ LangGraph 问答 Agent──▶ 分层讲解
              判定学科与知识点锚点 → 图谱聚合检索（模糊解析锚点 + 每类 top-N 截断）
              → 按 (pdf_id, 页码) 回表取讲义页原文 → 生成回答（标注教材来源）
```

抽取本体（跨学科通用 schema，见 `ingestion.py`）：章节、概念（拆解/易错/前置）、
公式（符号表/适用条件/推导步骤）、实验（器材/步骤/现象/结论/装置图解）、
题型（识别特征/解题模板/陷阱）、例题（编号/小标题/归属题型/结构化出处含页码，
原文不由 LLM 抄写，问答时按 `source.page` 回表取讲义页原文）、通法技巧。

同一对 `vector_db` / `graph_db` 可被多个学科反复调用 `build_knowledge_bases`
累积灌入，形成三科合一的知识库。**多本不同 PDF 累积进同一知识库**（如两本教材
都讲「比热容」）时的语义：同名知识实体（概念/公式/实验/题型/方法）是真同一
知识点，节点属性按「越建越全」合并（无序要点列表 union 去重、描述与步骤序列
保留更长一份、概念额外累积 `sources` 字段记录收录来源）；而**页码与例题编号
跨书会撞车**（两本书都有「第 15 页」「例17」），建库时须把 `pdf_id`（PDF 内容
哈希前 16 位，main.py 自动计算）并入讲义页切片键与例题节点键做来源隔离——
`main.py` 已自动传入，直接多次 `--stage build --pdf 书B.pdf` 即可安全累积。

抽取提速：LLM 抽取拆为**两批串行**（第一批知识体系 → 第二批题型与例题，并注入
第一批的概念名保证引用一致），且关闭思考模式（`enable_thinking=False`）；抽取
结果按「学科 + schema 版本 + 输入全文」哈希缓存于 `output/extract_cache/`，
相同输入重跑 **0 次 LLM 调用**。

**长文档增量建库**：页码区间可以直接开到整本书（几百上千页）而不会撑爆上下文——
`build_knowledge_bases` 内部自动分块、逐块抽取即落盘、注入滚动上下文保证命名一致、
并在建库前做规模预估与成本统计。完整机制见下一节「[长文档增量建库](#2-长文档增量建库l1--l2--l3--成本控制)」。

## 2. 长文档增量建库（L1 / L2 / L3 / 成本控制）

一次性把整本书（几百上千页）喂给推理 LLM 会超上下文、崩溃即全丢、且成本不可见。
`build_knowledge_bases` 因此重构为**分块增量**流水线，围绕四个目标分层实现：
**支持长文档 + 增量抽取知识体系 + 保证知识点间关系 + 成本可控**。

### L1 · 自动分块 + 逐块落盘（支持长文档 / 断点续跑）

- `_split_into_chunks(pages_data, max_chars=6000)`：按字符预算把输入页切成若干子块。
  **页面是原子单位**（每页讲义需以 `subject:Page:{pdf_id}:页码` 独立入向量库供例题
  回表，pdf_id 为空时退化为 `subject:Page:页码`），
  所以只在「页与页之间」切，绝不把某页的 `--- 第 N 页 ---` 标记与正文拆到两块：
  - 累加超过 `max_chars` 即切一刀；
  - 已攒到预算 60% 且下一页是章节标题（`#`/`##`/`###`）时提前切，避免新章节标题落在块尾；
  - 单页内容超预算时强制单独成块（不跨页拆正文）。
- `build_knowledge_bases` 逐子块循环：拼该块 Markdown → 查该块抽取缓存 → 未命中才做
  两批串行 LLM 抽取 → 写图 + 写向量 → **每块处理完立即 `graph_db.save()`**。
  一次 CLI 可能跑几十次 LLM，中途崩溃只丢当前块，已处理块均已持久化。
- **每个子块的抽取缓存 key 只由该子块自身内容决定**（`学科 + schema 版本 + 该块 Markdown`），
  因此**重复执行同一条命令 = 断点续跑**：已处理子块自动命中缓存、0 次 LLM 调用、直接写库。

### L2 · 滚动上下文注入（增量抽取 + 命名一致）

增量建库时模型每次只看到一个子块，看不到此前抽过什么，容易出现「同一概念被起不同
名字」「同一章节反复开新章」导致图谱隐性重复。处理每个**新**子块前，
`_gather_known_context` 会拉取两份「已知信息」注入两批 prompt：

- **全书已有章节**：该学科图谱里所有 Chapter 节点标题（轻量全量，封顶 120 条）；
- **已建库的相关概念**：用当前子块前 2000 字符对向量库做相似度检索
  （`filter={"subject": 学科, "type": "Concept"}`），只取 top-K（默认 12）条 name + 一句话描述，
  控制 prompt 体积不随全书概念总数线性增长。

prompt 要求：本批文本若命中上述列表中的同一概念/章节，`name`/`title` **必须逐字复用**，
严禁另起同义名；未列出的新概念按原文标准名词正常新建。
> 注：滚动上下文**不参与**抽取缓存 key。图增长后重跑仍复用早先缓存的 JSON（确定性、省钱），
> 因此上下文注入只在缓存未命中时执行。

### L3 · 关系保全 + 去重审计（保证知识点间关系）

- **跨块题型回退挂边（bugfix）**：例题的归属题型可能在前置子块已定义、本批未重复声明。
  `_write_graph` 在本批 `qt_keys` 查不到时**回退查全局持久化图**，命中则补挂
  `EXEMPLIFIED_BY` 边——否则「A 块定义题型、B 块出例题」会永久丢边。
- **疑似重复审计**：构建结束 `_audit_graph` 除列出空壳概念节点外，按名称相似度
  （`difflib`，阈值 0.82）扫描并报告疑似重复概念对，给出合并指引。
- **显式合并**：审计只报告不动库；人工核对后可用
  `graph_db.merge_concepts(subject, canonical, alias)` 把别名节点的全部关系按原方向重指到
  规范节点并删除别名（规范名不存在时整体改名，不丢属性），或
  `graph_db.find_similar_concept(subject, name)` 查最相似候选。
  > 有意取舍：不在写库前自动改写 LLM 输出的 name（就地替换风险高、缓存一致性难保证），
  > 改用「L2 预防 + 审计报告 + 显式 merge 兜底」组合。

### 成本控制（花钱前先亮规模，花钱后可见）

- **建库前预估**（`main.py _estimate_build`，只读缓存 + 本地统计，不调用任何模型）：
  打印「需新视觉调用次数 / 自动切几块 / 已缓存几块 / 需新抽取几块（每块约 2 次推理 LLM）」，
  随后 `[y/N]` 确认；`--yes` 跳过；非交互终端且需新调用时直接拒绝执行，防止误烧钱。
- **预算上限** `--max-chunks N`：单次最多处理 N 个**未命中缓存的新子块**（缓存命中不占额度），
  达到即主动停并提示「重跑同命令续跑」，配合预估分轮灌完整本书。
- **真实 token 统计**（`main.py TokenMeter`）：从响应 `usage_metadata`（回退
  `response_metadata.token_usage`）读取真实用量，视觉 / 推理两路分别透传并在结束时打印，
  服务端不回传 usage 的调用不计入。

### 相关命令行参数

| 参数 | 作用 |
|---|---|
| `--max-chars N` | 单子块字符预算（默认 6000）：输入页超过即自动切块 |
| `--max-chunks N` | 本次最多处理 N 个未命中缓存的新子块（缓存命中不占额度），达上限主动停 |
| `--yes` | 跳过建库前的规模预估确认（脚本 / 夜间批量自动放行） |

```powershell
# 整本教材分轮增量建库：先预估，每轮只处理 20 个新子块；再跑同命令即续跑（已缓存块不计费）
uv run python main.py --stage build --pdf 整本教材.pdf --start-page 13 --end-page 320 --subject math --max-chunks 20
```

## 3. 常用命令

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
# 换材料无需改源码，用命令行参数指定 PDF / 页码 / 学科 / 提问
uv run python main.py --stage build --pdf 教材.pdf --start-page 11 --end-page 12 --subject physics   # 提取并累加进双库
# 整本教材分轮增量建库：先预估，每轮只处理 20 个新子块，交互确认（--yes 跳过）
uv run python main.py --stage build --pdf 整本教材.pdf --start-page 13 --end-page 320 --subject math --max-chunks 20
uv run python main.py --stage ask   --query "请讲解可变电路的分析思路"                                # 仅问答，复用已持久化双库
uv run python main.py                                                                                  # 不带参数 = 内置默认示例
```

### 测试 / 自检

项目暂无正式测试套件，常用冒烟方式：

```powershell
# 全模块导入冒烟
uv run python -c "import main, ingestion, pdf_processor, config, agent.workflow, storage.graph_store, storage.vector_store; print('ALL IMPORTS OK')"

# 查看运行日志（控制台 INFO，文件 DEBUG）
Get-Content output\sida_agent.log -Tail 50
```

## 4. 重要目录与文件

| 路径 | 重要度 | 说明 |
|---|---|---|
| `main.py` | ★★★ | 流水线入口：提取 → 建库 → 问答；`--stage/--pdf/--start-page/--end-page/--subject/--max-chars/--max-chunks/--yes/--query` 参数化；建库前打印规模预估并确认（`--yes` 跳过），结束打印两路真实 token 消耗，换材料无需改源码 |
| `config.py` | ★★★ | 统一 LLM/Embedding 工厂；`.env` 中 base_url/key/model 在此生效 |
| `ingestion.py` | ★★★ | 核心：自动切子块（`_split_into_chunks`）、滚动上下文注入（`_gather_known_context`）、两批串行抽取 prompt、逐子块抽取缓存与落盘、双库写入编排、幽灵节点/疑似重复审计 |
| `agent/workflow.py` | ★★★ | LangGraph 问答工作流：学科判定 → 图谱检索 → 按页码回表讲义页 → 生成（意图/讲解双 LLM 分调优、答案来源标注） |
| `storage/graph_store.py` | ★★★ | `ScienceGraphStore` 图谱存储、`get_subgraph` 聚合检索（每类实体 top-N 截断）、概念锚点模糊解析、疑似重复概念合并（`merge_concepts`/`find_similar_concept`） |
| `pdf_processor.py` | ★★ | PDF 页渲染 + 视觉模型提取 Markdown，逐页缓存于 `output/pdf_extract/` |
| `storage/vector_store.py` | ★★ | Chroma 向量库初始化（collection `science_kb`，落盘 `output/vector_db/`） |
| `agent/state.py` | ★ | Agent 状态 TypedDict（query / target_subject / 检索结果等） |
| `logger.py` | ★ | 控制台 + `output/sida_agent.log` 双通道日志 |
| `.env` | ★★ | 模型服务配置（不入库）；`.env` 缺失或 key 为空时启动会给出指引报错 |
| `output/` | — | 运行产物：日志、PDF 提取缓存（`pdf_extract/{pdf_id}/pXXXX_{ver}.md`）、抽取缓存（`extract_cache/{hash}.json`）、向量库（`vector_db/`）、图谱（`knowledge_graph.json`） |

## 5. 其它说明

- PDF 提取缓存目录结构 `output/pdf_extract/{pdf_id}/p{页码}_{版本}.md`，`pdf_id` 为 PDF
  内容 SHA-256 前 16 位，与姊妹项目 `knowledge_extract/extract_pdf` 同算法；文件名版本
  取自 `pdf_processor._EXTRACT_VERSION`，调整 PROMPT、渲染分辨率或后处理时递增即可让
  旧页缓存自动失效，无需手动删目录。
- 双库均跨进程持久化：向量库落盘 `output/vector_db/`（Chroma PersistentClient），
  图谱落盘 `output/knowledge_graph.json`（node_link JSON）。`build_knowledge_bases`
  结束时自动 `save`，`main.py` 启动时 `ScienceGraphStore.load()` 自动载入。因此可
  分次喂入不同学科/教材 PDF 持续累积成三科知识库，也可另起独立只读问答进程。
- 清空知识库：删除 `output/vector_db/` 与 `output/knowledge_graph.json`；只删
  `extract_cache`/`pdf_extract` 则下次重建重新走（缓存命中的）抽取流程。
- 向量写入以 `metadata.id` 为键幂等 upsert，重复重建不会在 Chroma 中累积重复切片；
  修改抽取 schema 后请递增 `ingestion._EXTRACT_SCHEMA_VERSION` 使旧抽取缓存失效，
  修改 PDF 提取 PROMPT/渲染参数/后处理后请递增 `pdf_processor._EXTRACT_VERSION`
  使旧页缓存失效。
- 图谱检索截断：`get_subgraph` 对公式/实验/题型/方法/例题每类默认返回 top-8
  （`storage/graph_store._DEFAULT_MAX_PER_KIND`，调用时传 `max_per_kind=None` 关闭），
  命中「枢纽概念」（关联几十条实体）时防止撑爆下游 prompt；examples 截断会连带
  减少按页回表的讲义页数。
- 双 LLM 分调优：`agent/workflow.py` 的意图判定与最终讲解各用一个 `get_reasoning_llm`
  实例——判定走低温 / 小 `max_tokens` / 关思考（只输出一行 JSON，短平快），讲解保留
  默认思考与大 token 预算（长输出），二者参数互不干扰。
- 答案可追溯性：生成 prompt 注入「本次检索命中情况」（图谱命中与否 + 讲义命中页码），
  输出规范要求模型对取自例题原文的内容标注「（见教材第 X 页）」、取自图谱各区块的标注
  「（教材知识点，图谱收录）」（图谱实体抽取时不记页码，只能到图谱粒度）、自行补充的
  学科知识另起「【补充说明·教材未涉及】」段；仅当图谱与讲义双双未命中时，才在正文开头
  声明为通用讲解——避免无教材支撑的内容以同等自信误导学生 / 家长。
