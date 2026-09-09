# sida-agent · 初中理科全科知识库问答 Agent

把初中**物理 / 化学 / 数学**教材 / 讲义 PDF，用视觉大模型提取、再用推理大模型结构化抽取，
沉淀为一套「知识图谱 + 向量库」双库，并对学生提问生成可溯源到教材页码的分层讲解。

---

## 1. 这个项目解决什么问题 / 核心能力

- **把纸质讲义变成能问答的知识库**：PDF 每一页先用多模态视觉模型转成结构化 Markdown
  （保留印刷文字、LaTeX 公式、表格、电路图 / 实验装置图的文字描述、手写批注），
  再由推理模型提炼成概念 / 公式 / 实验 / 题型 / 例题 / 方法等教研实体。
- **答案能指到教材第几页**：例题正文不由模型抄写，而是在抽取时记下出处页码，
  问答时按页码回向量库取讲义原文，生成的讲解里标注「（见《教材名》第 X 页）」。
- **问公式名 / 集合名词也能命中**：提问的锚点未必是概念名——可能是公式名
  （「三角函数的倍角公式」）、也可能是一族实体的统称（「两角和公式」= 正弦/余弦/正切
  三条）。检索链路对锚点做「精确 → 模糊 → 同族展开」三级解析，把整族内容一次捞回，
  避免「知识在库里却答未收录」（见 3.8）；带度数标记的提问（「15°、22.5°三角函数值」）
  与挂在兄弟概念名下的公式（「18°三角函数」）由度数族匹配与概念级下钻兜住（见 3.9）。
- <span style="color:red">**三科共享一套双库、可反复累积**：同一份图谱 + 向量库可被物理 / 化学 / 数学、
  多本不同 PDF 多次灌入；同名概念按「越建越全」合并，不同书的同页码 / 同题号靠
  `pdf_id` 前缀隔离，互不覆盖。</span>
- **整本书也能喂**：页码区间可开到几百页，内部按字符预算自动切子块、逐块抽取即落盘，
  配合缓存天然支持断点续跑，不必一次成功。
- **花钱前先亮规模**：建库前做一次只读「规模预估」（不调模型），统计要新增多少次视觉 /
  推理调用，交互确认或用 `--yes` 放行；结束打印两路真实 token 消耗。
- **三种用法**：单轮问答（`ask`）、一次性讲解（`all`）、可续聊的多轮对话（`chat`，
  会话落盘、超预算自动压缩进摘要、能按题目原文「找题」）。

---

## 2. 整体架构

数据从输入到输出经过「视觉提取 → 结构化抽取入库 → 检索问答」三个阶段，
入口都是 `main.py`，由 `--stage` 决定走哪几段。

```mermaid
flowchart TD
    PDF[教材 PDF] -->|① pdf_processor.extract_pdf_pages_as_markdown| MD[逐页 Markdown]
    MD -->|缓存 output/pdf_extract/pdf_id/p页码_v2.md| MD
    MD -->|② ingestion.build_knowledge_bases| EXTRACT[两批串行抽取 + 滚动上下文]
    EXTRACT -->|缓存 output/extract_cache/key.json| EXTRACT
    EXTRACT --> GRAPH[(知识图谱 ScienceGraphStore<br/>output/knowledge_graph.json)]
    EXTRACT --> VECTOR[(向量库 Chroma<br/>output/vector_db)]

    Q[学生提问] -->|③ agent.workflow.create_circuit_agent| INTENT[analyze_intent<br/>判定学科+意图+锚点]
    INTENT -->|concept| TRAVERSAL[graph_traversal<br/>图谱聚合检索]
    INTENT -->|find_problem| SEARCH[search_problems<br/>整页原文检索]
    INTENT -->|offtopic| CHITCHAT[respond_chitchat]
    GRAPH --> TRAVERSAL
    VECTOR --> TRAVERSAL
    TRAVERSAL --> FETCH[fetch_chunks<br/>按 pdf_id+页码 回表取原文]
    VECTOR --> FETCH
    FETCH --> GEN[generate_response<br/>分层讲解+来源标注]
    SEARCH --> GENQ[generate_problem_response]
    GEN --> ANS[讲解 Markdown<br/>output/answers/]
    GENQ --> ANS
    CHITCHAT --> ANS
```

- **阶段①（视觉）**：`pdf_processor.py`，PDF 页 → Markdown，逐页缓存。
- **阶段②（抽取入库）**：`ingestion.py`，Markdown → 双库，按子块增量、抽取结果缓存。
- **阶段③（问答）**：`agent/workflow.py`（LangGraph），提问 → 检索 → 生成分层讲解。
  `--stage chat` 时额外经 `chat_session.py` 挂 SqliteSaver 做会话持久化。

---

## 3. 关键技术方案

### 3.1 视觉提取与逐页缓存（`pdf_processor.py`）

- `extract_pdf_pages_as_markdown(pdf_path, start_page, end_page, ...)`：用 PyMuPDF
  把每页渲染成 PNG（仅内存，长边上限 `MAX_LONG_EDGE=3000`、放大倍数上限 `MAX_ZOOM=4.0`），
  连同一段通用结构化提示词 `PROMPT` 交给视觉模型。
- **缓存命名**：`output/pdf_extract/{pdf_id}/p{页码:04d}_{_EXTRACT_VERSION}.md`，
  当前 `_EXTRACT_VERSION="v2"`。`pdf_id = _pdf_id(pdf)` 是 **PDF 文件内容 SHA-256 的前 16 位**
  （与 `knowledge_extract/extract_pdf` 同算法，两项目可共用缓存目录）。
- **判定「已提取」**：`extract_pdf_pages_as_markdown` 循环里，页缓存文件存在且
  `read_text().strip()` 非空即直接读缓存、不再调模型。因此**中断重跑自动跳过已完成页**。
  版本号 `v2` 变更（`PROMPT` / 渲染参数 / 后处理调整）会让旧缓存整体失效。
- **后处理**：`_fix_math` 把模型偶发的成对 `$$ ... $$`（跨行）折叠成单行行内 `$...$`，
  避免块级公式在部分渲染器里失效。
- **重试 / 超时 / 失败处理**：`_invoke_llm` 外层重试 `LLM_RETRIES=2`（最多 3 次尝试），
  失败按 `wait = 2*(attempt+1)` 秒退避（2s、4s）；底层 `ChatOpenAI` 用 `max_retries=0`
  避免双份重试；单次请求 `LLM_TIMEOUT=300s`、`MAX_TOKENS=8192`。三次仍失败抛
  `RuntimeError`。模型返回空内容时**不写缓存**并记 warning（该页下次会重抽）。

### 3.2 长文档自动分块（`ingestion._split_into_chunks`）

- 页面是**原子单位**（每页需以 `subject:Page:{pdf_id}:{页码}` 独立入向量库供例题回表），
  因此**只在页与页之间切**，不跨页拆内容。切分条件：
  - 累加后 `cur_len + len(content) > max_chars`（默认 `6000`，即 `--max-chars`）；或
  - 已攒到预算的 `_CHUNK_HEADING_FACTOR=0.6`（即 3600 字）且**下一页是 Markdown 标题行**
    （正则 `^#{1,3}\s`）时提前切一刀，避免把新章节标题留在块尾。
  - 单页内容超预算时强制单独成块。

### 3.3 两批串行抽取 + 滚动上下文（`ingestion._extract_chunk_data`）

- 每个子块做**两批** LLM 抽取：`_build_knowledge_prompt`（章节 / 概念 / 公式 / 实验 / 方法）
  → `_build_question_prompt`（题型 / 例题 / 补充关系，并注入第一批的概念名保证引用一致）。
- 推理模型经 `get_reasoning_llm(enable_thinking=False)` 创建（追求吞吐，关思考模式）。
- **滚动上下文**（`_gather_known_context`）：处理子块前注入两块信息，让模型「记得」此前建过什么，
  解决跨子块命名不一致 / 章节重复开章：
  - 章节：从图谱取该学科所有 `Chapter` 节点标题（上限 `_KNOWN_CONTEXT_CHAPTERS_CAP=120`）；
  - 相关概念：用当前子块**前 2000 字符**去向量库做相似度检索（限定 `subject`+`Concept`），
    取 top `_KNOWN_CONTEXT_TOP_K=12` 条「名称：描述前 40 字」。
  - 注入块（`_build_context_block`）明确要求：命中已有概念 / 章节时 `name`/`title` **逐字复用**，
    不得另起同义名。检索失败时捕获异常、忽略并继续（不阻断抽取）。

### 3.4 抽取缓存与断点续跑（`ingestion`）

- **缓存 key**：`_cache_key(subject, full_markdown)` =
  `sha256(f"{subject}|{_EXTRACT_SCHEMA_VERSION}|{full_markdown}")[:16]`，当前
  `_EXTRACT_SCHEMA_VERSION="v3"`。缓存文件 `output/extract_cache/{key}.json`。
- key **只由该子块自身内容决定**，所以重跑同一条命令时已处理子块直接命中缓存、0 次 LLM 调用。
  `_persist_chunk` 每处理完一个子块就 `graph_db.save()` 一次——中途崩溃只丢当前子块，
  已处理子块均已持久化 + 缓存，配合上条即**天然断点续跑**。
- JSON 解析健壮性（`_extract_json`）：解析失败时追加 `_JSON_RETRY_HINT` 重试一次
  （`max_attempts=2`）；若是字符串里夹带未转义反斜杠（模型把 LaTeX 记法原样抄入），
  先经 `_repair_json_escapes` 就地修复再解析；两次都失败才抛 `RuntimeError`。
- 字段类型归一化（`_normalize_extracted`）：写库 / 建向量前统一执行一次（**缓存命中的旧脏数据也覆盖**），
  把「应为数组却是字符串」的字段告警置空（杜绝 `list("中文")` 拆成单字符），
  `symbols` 仅保留 dict 元素，例题 `source` 非 dict 置 `{}`。

### 3.5 跨块 / 跨来源合并策略（`ingestion._ensure_entity`）

实体节点键 = `node_key(subject, kind, name)` = `f"{subject}:{Kind}:{name}"`（学科命名空间隔离，
避免物理「分子」与化学「分子」互相覆盖）。同名实体第二次写入按「**越建越全**」逐属性合并
（不再是先到先得的 `setdefault`）：

| 字段类别 | 合并规则 | 判断依据 |
|---|---|---|
| 旧值缺失 / 为空（`None`/`""`/`[]`/`{}`） | 补上新值 | 修掉空壳节点永不更新 |
| 本次传入为空值 | 保留旧值不动 | 空抽取不冲刷已收录内容 |
| 无序列表（`breakdown`/`common_mistakes`/`sources`/`related_concepts`…） | union 去重保序 | 要点集合可累加 |
| **顺序敏感字段**（`_SEQUENCE_FIELDS` = `derivation`/`template`/`steps`） | 保留更长的一份 | 两套步骤 union 会串成乱序流程 |
| 标量字符串 | 保留更长的一份 | 更详细的表述优先 |
| dict 字段（例题 `source`） | 只补缺失 / 为空的键，已有键先到先得、不覆盖 | 锁定首次抽取的真实 `page`，防「新标题配旧页码」缝合怪 |

**跨 PDF 来源隔离**：`main.py` 把 `pdf_id`（内容哈希前 16 位）传入 `build_knowledge_bases`，
用于两类无来源维度的键：
- 讲义页切片键 → `subject:Page:{pdf_id}:{页码}`（`_build_page_docs`）；
- 例题节点键 → `{pdf_id}:{loc}:{题号}`（`_write_graph`），`loc` 取 `source.page`，缺页时退化为
  `na-{pdf_id}-{序号}`。题号降级为展示属性 `number`，不再充当唯一标识
  （教辅每章从「例1」重编，同书跨章也会撞号，故必须带页定位）。

概念 / 公式 / 实验 / 题型 / 方法等知识实体**同名即同一知识点，不做来源隔离**，
靠上表合并累积；`sources` 属性累积 `pdf_id` 列表，作为「多本教材共收 = 核心考点」信号，
问答时反查 `PdfSource` 注册表把「图谱收录」升级为「收录于《教材名》」。

### 3.6 审计 / 异常检测（`ingestion._audit_graph`）

整轮建库结束后（`graph_db.save()` 前）审计本学科：
- 列出**空壳概念节点**（无 `description`，多为 `prerequisites` 前置引用或历史脏数据），报告前 20 个；
- 用 `difflib.SequenceMatcher` 两两比较**有描述**的概念名，相似度 ≥ `_DUP_CONCEPT_RATIO=0.82`
  的报为**疑似重复概念对**（前 10 对）。审计**只报告不动库**，用户核对后可显式
  `graph_db.merge_concepts(subject, canonical, alias)` 合并。
- 防幽灵节点：`_write_graph` 里 `extra_relations` / `related_concepts`（经 `_link_concept_refs`）/
  例题 `question_type` 引用不存在的概念时**告警跳过、不盲建空壳**（例外：`prerequisites`
  仍用 `_ensure_entity` 建占位 Concept，作为跨 PDF 分次累积的合理前向声明）。
- **防孤儿节点（`_resolve_ref_key` 跨种类解析）**：`related_concepts` 名义上填「概念名」，
  但 LLM 实测常填**兄弟实体名**（如「二倍角公式」→「两角和的正弦公式」）。早期只认
  `Concept`，这类引用被整体丢弃，公式节点随即成为无任何边的**孤儿**——图谱里看得见、
  检索链路永远走不到（`get_subgraph` 只从概念节点出发）。现按 `_REF_KINDS`
  （Concept → Formula → Experiment → QuestionType → Method → Example）**逐个精确查找**，
  Concept 优先；命中非 Concept 时改用补充关系 `REL_EXTRA` 连边，保住连通性。
  仍然只做精确匹配、不新建节点（防幽灵与防孤儿两个目标同时成立）。
- **同块共现兜底**：某实体一个概念邻居都没有时（引用全是兄弟实体、或整段为空），
  按「同块共现」挂到**本块声明的**概念上（`REL_EXTRA`），保证每个实体都能从概念出发被
  检索到。日志：`[ingestion] 二倍角公式 无概念邻居，按同块共现挂到 2 个概念: …`。
  这两项是 3.8 实体锚点检索的**数据前提**：即便仍有历史遗留孤儿，`get_entity_subgraph`
  也能直接从实体入口命中，不再依赖它是否挂上了概念。

### 3.7 检索与问答链路（`agent/workflow.py`）

LangGraph 状态机（`create_circuit_agent` 编译），节点：
`manage_context → analyze_intent → {graph_traversal → fetch_chunks → generate_response
| search_problems → generate_problem_response | respond_chitchat} → END`。
分流由 `route_by_intent` 按意图决定。

- **`analyze_intent`**：一次低温短输出 LLM 调用（`temperature=0.0`、`max_tokens=128`、关思考），
  判定 `{subject, intent, concept, search_text}`；`intent` 三选一 `concept`/`find_problem`/`offtopic`。
  解析用 `_parse_intent`（非标准 JSON 时退回正则提取字段；任何回退到 `physics` 都记 warning）。
- **`graph_traversal`（concept 链路）四级兜底**：
  1. 概念名直接命中 → `get_subgraph` 做 1~2 跳聚合（`_resolve_concept` 内部还有一次
     去教学尾缀 → 双向包含 → `difflib≥0.6` 的模糊解析；并从兄弟概念下钻一层挂载实体、
     各桶按锚点相关性排序后截断，见 3.9）；
  2. 返回的 `concept` 为 `None`（图谱里根本没有这个概念节点）→ 把锚点当**非概念实体名**
     解析：`get_entity_subgraph` 以公式 / 题型 / 方法 / 例题名为入口聚合（见 **「3.8 实体锚点
     解析与集合名词同族展开」**）。这一级必须排在旧的 `get_by_name` 之前——`get_by_name`
     要求实体已挂到概念上，历史建库遗留的**孤儿公式节点**恰恰挂不上，会直接漏掉；
  3. 实体名仍未命中 → 按 题型 → 方法 → 例题 的优先级 `get_by_name` 反查其相邻 Concept，
     改用该概念重新聚合；
  4. 命中的是**空壳概念**（无 description/breakdown 且聚合不到题型 / 例题）→
     阶段一按提问原文在 1 跳邻居概念里挑字面命中的内容枢纽重定向；无命中则阶段二用
     `resolve_chapter`（剥教学语气词 + 章节主题词互含判定）做**整章聚合**
     `get_chapter_subgraph` 兜底。
- **`fetch_chunks`**：按命中例题的 `(pdf_id, page)` 组向量键 `subject:Page:{pdf_id}:{页码}`，
  用 `vector_db.get(where={"id": pk})` 精确回表取讲义页原文；未命中记 warning，
  缺 `page` 的例题不拿空壳冒充原文。
- **`generate_response`**：把图谱上下文（概念拆解 / 公式 / 实验 / 题型 / 方法 / 先修 / 后续）
  与回表原文拼进提示词，强约束「每个知识点必须能在六个区块里找到出处，资料没有的写
  『当前教材资料未收录』」，流式生成。图谱每类关联实体默认截断 top `_DEFAULT_MAX_PER_KIND=8`
  防 prompt 膨胀；截断前先按锚点 token 相关性排序，与提问直接相关的实体不被截掉
  （见日志「关联 examples 共 94 条，截断至 top-8」，机制见 3.9）。
- **`search_problems`（find_problem 链路）两级**：先 Chroma `where_document={"$contains": text}`
  整页逐字命中；无命中再语义检索取 top `_SEARCH_PROBLEM_RERANK_K=10` 候选，
  按 `_bigram_overlap`（相邻字符二元组重合度）重排后取 top `_SEARCH_PROBLEM_TOP_K=3`，
  由 `generate_problem_response` 在整页原文里定位目标题、原题呈现并简析。
- **`respond_chitchat`（offtopic）**：不触发任何检索，一两句轻量回应并引导回学习。

### 3.8 实体锚点解析与集合名词同族展开（`storage/graph_store.py`）

**要解决的问题**：意图 LLM 提炼出的锚点，未必是图谱里的 Concept 名。实测三类高频失配——

| 提问锚点 | 图谱里的真实节点 | 为什么概念入口落空 |
|---|---|---|
| `三角函数的倍角公式` | `Formula:二倍角公式` | 它是**公式**不是概念；且比节点名多了首字 |
| `两角和公式` | `Formula:两角和的正弦公式`<br/>`两角和的余弦公式` / `正切公式` … | 锚点是**一族实体的统称**，库里根本没有这个名字 |
| `锐角三角函数` | `QuestionType:锐角三角函数的定义` 等 | 内容挂在题型上，`Concept:锐角三角函数` 无邻居 |

`get_entity_subgraph(subject, name, kinds=(Formula, QuestionType, Method, Example))`
以非概念实体为入口做检索，锚点解析按「**精确 → 单点模糊 → 同族展开**」推进：

**① 精确同名**：逐个 `kind` 拼 `node_key` 查图，命中即聚合，最准也最省。

**② 单点模糊解析（`_resolve_entity`）**：概念与实体共用同一套三级放宽，靠参数区分口径。

```
1) 剥教学修饰尾缀（_CONCEPT_SUFFIXES：分析/思路/方法/讲解…）后精确命中
2) _near() 双向包含 / 后缀近似，取 difflib 最高者
3) difflib 相似度兜底，阈值 ratio_threshold
```

两个关键约束（都是踩坑后收紧的）：

- **阈值分档**：概念用 `_FUZZY_THRESHOLD=0.6`，非概念实体用 `_FUZZY_THRESHOLD_ENTITY=0.8`。
  中文短名的 `difflib` 过于宽松——「锐角三角函数」会以 **0.667** 误配到公式
  「特殊角三角函数值表」，实体口径必须收紧。
- **泛词长度闸门**：`_near()` 的真包含判定要求候选名与锚点**双方 ≥3 字**。否则 2 字集合名词
  （「公式」「方法」）与任何含该词的节点都构成包含关系，会把「公式」误配到「二倍角公式」。
  完全同名（`cand == name`）不受此限制。
- **`allow_suffix=True`（仅实体路径开启）**：允许「后缀近似」——只差首字的写法
  （`三角函数的倍角公式` ↔ `二倍角公式`）靠包含抓不住，用
  `name.endswith(cand[1:])` / `cand.endswith(name[1:])` 兜住；该判据只在尾部高度重合时
  成立，不会像覆盖率那样把「锐角三角函数」误配到「特殊角三角函数值表」。

**③ 同族展开（`_resolve_entity_family`）**：处理集合名词。单点相似度在这里必然失效——
`difflib("两角和公式", "两角和的正弦公式") = 0.769 < 0.8`，而阈值又不能降（见上）。
改用**结构判据**：

```
在同科同 kind 的候选名里统计各前缀（长度 ≥2）的出现次数
→ 取「被 ≥2 个候选共享」且出现在锚点中的前缀作族标记
→ 多个候选标记时，优先取在锚点中位置最靠后的（越靠后越具体）
→ 标记长度 ≥ _FAMILY_MIN_LEN=3 且命中 ≥2 条，才认定成族
```

- 「两角和」被 5 条公式共享 → 一次捞回整族；
- 「两角差的正弦公式」的「两角差」只被 1 条共享 → **不触发**族展开，交回单点解析，天然排除；
- 位置最靠后优先，是为了防止 `三角函数的两角和公式` 被泛化的「三角函数」劫持
  （「三角函数」前缀虽共享，但定位能力远弱于「两角和」）。
- **已知盲区**：「非特殊角（15°/22.5°）三角函数的几何构造法」这类**包含标记但不以标记
  开头**的命名，前缀判族抓不住——由度数标记族匹配补足（见 3.9 修复 1）。

②③ 的结果**合并**成锚点集合（不是短路取第一个），保证既不漏单条、也不漏整族。

**④ 聚合（`_build_entity_result`）**：把任意多个锚点摊平进与 `get_subgraph` **同构**的
结果字典（下游 `fetch_chunks` / `generate_response` 无需分支）：

```
锚点自身          → 按 _BUCKET_OF 归入 formulas/question_types/methods/examples
1 跳邻居（出+入）  → Concept 进 related_concepts（带 relation），其余按 _BUCKET_OF 归桶
2 跳              → 沿题型/方法取挂载的 Example
各桶按 max_per_kind（默认 _DEFAULT_MAX_PER_KIND=8）截断
```

日志形如：`非概念锚点 三角函数的两角和公式 命中 8 个实体，聚合到 公式 8/题型 3/方法 0/例题 5`。

**⑤ 命中判据必须覆盖全部区块（`generate_response`）**：这是「库里明明有、却回答未收录」的
**直接原因**。`retrieval_status` 早期只看 `concept or concepts`，而非概念锚点命中时这两个字段
恒为 `None` → 状态被判成「未命中」→ 模型严格按输出规范第 6 条拒答。现改为六桶全查：

```python
graph_hit = bool(concept or concepts or g_ctx.get("formulas")
                 or g_ctx.get("experiments") or g_ctx.get("question_types")
                 or g_ctx.get("methods") or g_ctx.get("examples"))
```

并在输出规范里补一句：图谱「命中」即表示六区块至少一个含实际内容，此时**必须**基于这些
区块作答，不得因【知识点定位】为空（锚点是公式/题型名而非概念名时属正常）就判定未收录。

**实测**（图谱 1235 节点 / 3331 边）：

| 锚点 | 聚合结果 |
|---|---|
| `两角和公式` | 公式 8 |
| `两角和的正弦、余弦、正切公式` | 公式 7 |
| `三角函数的两角和公式` | 公式 8 / 题型 3 / 例题 5 |
| `三角函数的倍角公式` | 公式 1 / 题型 3 / 例题 5 |
| `锐角三角函数` | 题型 4 / 例题 7 |
| `量子纠缠` / `公式` | `None`（正确判为未命中） |

### 3.9 度数族匹配与概念级下钻（`storage/graph_store.py`）

**要解决的问题**：走完 3.8 的解析链 + 3.7 的四级兜底之后，仍有两类提问「知识在库里、
图上也连通，就是检索走不到」——

| 提问锚点 | 现象 | 根因 |
|---|---|---|
| `15°、22.5°三角函数值` | 同族展开只捞回 3 条题型，公式 0 / 方法 0 | 真实方法名「非特殊角（15°/22.5°）三角函数的几何构造法」**包含但不以「三角函数」开头**，3.8③ 前缀判族抓不住；且公式只直接挂在概念上，题型锚点够不到 |
| `18°三角函数` | 模糊解析到 `锐角三角函数`，但 top-8 公式里没有 `18°角的正弦值` | 该公式挂在**兄弟概念**「特殊角的三角函数」名下，原二跳只沿题型/方法取例题、不跨概念；且截断按**插入序**，相关公式被任意挤掉 |

**修复 1 —— 度数标记族匹配**（`_DEGREE_RE`，加在 `_resolve_entity_family` 末尾）：
锚点含 `15°`/`22.5°` 这类角度数字时，度数是比共享前缀更强的判别信号——按「候选名
**任意位置**含该度数」收族，不要求 `startswith`；且与「两角和」需 ≥2 条共享才成族不同，
**单个命中也算**（度数本身就是具体标识，不存在泛词劫持）。日志：
`度数标记 ['15°', '22.5°'] 命中 N 个 QuestionType`。

**修复 2 —— 概念级下钻**（`_drill_concept_entities`，接在 `get_subgraph` 一跳收集之后）：
从 1 跳的**后续 / 关联概念**（先修概念不下钻，通常更基础）各再取一层挂载实体：
出边按 `_DRILL_OUT_REL`（公式 / 实验 / 方法），入边按 `TRACES_TO`（题型）。
下钻结果**追加在自身实体之后**，不稀释原本命中的内容。

**配套 —— 相关性截断**（`_anchor_tokens` / `_node_relevance`，接进 `_capped`）：
每桶截断 top-N 前，按锚点判别 token（度数数字 / 连续中文 / 连续西文段，长度 ≥2）的
命中数做**稳定排序**：锚点 `18°三角函数` 的 token `["18°","三角函数"]` 让
`18°角的正弦值`（命中 2 个）顶到最前；同分保持原序（自身实体仍优先于下钻结果）；
锚点无 token 命中（整章聚合等）时退化为原截断行为。注意 `anchor_name` 必须在
`_resolve_concept` **之前**捕获——模糊解析会覆盖 `concept_name`。

**实测**（图谱 1374 节点 / 3658 边）：

| 锚点 | 修复前 | 修复后 |
|---|---|---|
| `15°、22.5°三角函数值`（实体锚点） | 公式 0 / 题型 3 / 方法 0 | 公式 2（`15°角的正切值`、`22.5°角的正切值`）/ 题型 4（含「非特殊角（15°/22.5°）…几何构造法」）/ 例题 7（含 P37_Ex2、P37_Ex3） |
| `18°三角函数`（概念入口） | top-8 公式无 18° | `18°角的正弦值` 进入 top-8，题型含「黄金分割相关角（18°/36°）…」，例题含 P38_Ex1 |
| 回归 `两角和公式` | 公式 8 | 公式 8（不变） |
| 回归泛词 `公式` | `None` | `None`（不误命中） |

### 3.10 多轮对话与会话持久化（`chat_session.py` + `workflow.manage_context`）

chat 模式**没有自建对话表**，保存职责完全交给 LangGraph 的 **checkpointer** 机制，
落盘为单个 SQLite 文件。三层机制分别是：

**① 对话保存 = LangGraph Checkpointer 快照（`chat_session.open_saver`）**

```
main.py --stage chat
  └─ with open_saver() as saver:                     # 单连接包住整个 REPL 生命周期
       agent = create_circuit_agent(..., checkpointer=saver)   # workflow.compile(checkpointer=...)
       └─ 每轮：config = {"configurable": {"thread_id": sid}}
                agent.stream({"messages": [HumanMessage(提问)]}, config)
```

- `agent.stream` 带上 `thread_id` 后，LangGraph 在**每个节点执行完**把整个图状态
  （`channel_values`：`messages`、`history_summary`、意图、检索结果等）序列化成一个
  checkpoint 写入 `output/chat/checkpoints.sqlite`——每问一句磁盘上就多一层快照，
  随时 `Ctrl+C` / `/exit` 都不丢已完成轮次。
- `messages` 是**累积通道**（add 语义）：新一轮提问与各生成节点回写的 AIMessage 逐轮追加；
  下一轮执行前 checkpointer 自动恢复全部历史消息，模型因此「记得」之前聊过什么。
- **续聊 = 复用 thread_id**：`--session s-xxxx` 只是把既有 id 传进 config，
  LangGraph 自动从 sqlite 取该 thread 最新 checkpoint 恢复状态，无需手工加载。
- 读取快照不依赖 `SqliteSaver.list` 的顺序承诺：`_latest_tuple` 遍历该会话全部
  checkpoint 按 `ts` 取最大；`--list` 用只读 SQL `SELECT DISTINCT thread_id FROM checkpoints`
  列会话；`/export` 从最新快照同时取 `messages` 与 `history_summary` 导出 Markdown。
- REPL 内置命令：`/exit` `/quit` `/q` `退出` `再见`、`/new`、`/export`、`/list`、
  `/session <id>`、`/help`。会话 id 形如 `s-` + uuid 前 12 位（`_new_thread_id`）。

**② 超预算压缩 = 会话内记忆管理（`manage_context_node`）**

图的入口第一个节点即 `manage_context`（START → manage_context → analyze_intent）：

- `messages` 累计字符超 `_CHAT_HISTORY_BUDGET_CHARS=12000` 时，从末尾往回保留最近消息
  （保底保留本轮提问），被丢弃的旧消息返回 `RemoveMessage(id=...)` **从通道里真删掉**
  （下一轮 checkpoint 里就不再有它们）；
- 被删的对话经独立的 `summary_llm` **增量压缩**进 `history_summary` 通道（同样被
  checkpointer 持久化）；摘要失败只记 warning、保留旧摘要继续，不阻断主链路；
- 后续检索 / 生成节点把 `history_summary` 以「对话背景」形式注入 prompt，
  保证每次 LLM 输入有界。
- 因此**磁盘上的会话记录 = 未截断的最近消息 + 更早对话的摘要**，两者都在 checkpoint 里，
  `/export` 时摘要置于导出文件头部。

**③ 对应 Agent memory 模型的哪一层**

| Agent memory 概念 | 本项目对应物 | 性质 |
|---|---|---|
| 短期记忆（thread 级会话记忆） | checkpointer 持久化的 `messages` 通道 | 快照式逐轮落盘，`thread_id` 隔离 |
| 上下文窗口管理（summarization memory） | `manage_context` 预算截断 + `history_summary` 增量摘要 | 会话内压缩记忆 |
| 长期记忆（跨会话） | **无对话式长期记忆**；跨会话持久层是教材知识库（图谱 + 向量库） | RAG 语料，来自 PDF 建库而非对话 |

注意第三行：换一个 `thread_id` 后，新会话只能用到双库知识，**不会**检索或继承旧会话
聊过什么（旧会话仅能靠 `--session` 续聊或 `/export` 取回）。单轮模式（`ask`/`all`）
不传 checkpointer，完全无对话记忆，只把答案存 `output/answers/*.md`。

### 3.11 成本 / 资源控制（`main.py`）

- **规模预估（干跑）**：`_estimate_build` 只读逐页缓存 + 本地统计，**不调任何模型**，
  按与真实运行相同顺序遍历页，算出视觉侧（已缓存页 / 需新调用 / 因 `--max-new-calls` 被截断的剩余页）
  和推理侧（切几个子块 / 已缓存块 / 需新抽取块 ≈ ×2 次推理调用）。含未提取页时按平均页长
  （缓存内容均值，无缓存则 `_ESTIMATE_UNKNOWN_PAGE_CHARS=2000`）预演切块，实际块数可能略有出入。
- **放行确认**：`_confirm_build` 全部命中缓存直接放行；有新调用时交互确认 `[y/N]`，
  `--yes` 跳过；非交互终端（`not sys.stdin.isatty()`）且有新调用时**报错拒绝**并提示缩小范围或加 `--yes`。
- **分批消费**：`--max-new-calls`（视觉侧新提取页数上限）、`--max-chunks`（推理侧新子块数上限）
  相互独立，达上限主动停，已完成部分已落盘 / 缓存，重跑同命令续跑。
- **真实消耗统计**：`TokenMeter` 从响应 `usage_metadata`（回退 `response_metadata.token_usage`）
  取 input/output tokens；建库结束 `_report_meters` 打印视觉 / 推理两路。**服务端未返回 usage
  的调用不计入**（见「已知局限」）。

### 3.12 双库持久化（`storage/`）

- **向量库**（`storage/vector_store.get_vector_store`）：`Chroma`，collection 默认 `science_kb`，
  `persist_directory` 默认 `output/vector_db`（PersistentClient 自动落盘 / 加载，显式传 `None` 回退内存库）。
  每条 `Document.metadata` 至少含 `id`（与图节点键一致，供回表）、`subject`、`type`。
  写入用 `add_documents(docs, ids=[...])`，按 id **幂等 upsert**。
- **知识图谱**（`storage/graph_store.ScienceGraphStore`）：运行期内存 `nx.DiGraph`；
  `save()` / `load()`（classmethod）用 JSON node_link 格式（**必须带 `edges="links"`**，
  跨 networkx 版本稳定），默认 `output/knowledge_graph.json`。`load` 缺文件 / 解析异常回退空库。
  向量库靠 Chroma 自动持久化，图谱需**显式 save** 才能跨进程累积。
- **教材名注册表**：`register_pdf_name(pdf_id, name)` 存为 `meta:PdfSource:{pdf_id}` 节点
  （伪学科 `meta`，不与三科冲突），`pdf_names()` 反查全表。`--book` 传入教材显示名，缺省用文件名去扩展名，
  重复登记同名 `--pdf` 即改名覆盖。
- **去重 / 合并方法**：`find_similar_concept`（同科内找最相似已建概念，默认阈值 0.6，
  可跳过空壳）、`merge_concepts`（把 alias 的入边 / 出边按原关系重指到 canonical，
  再按「越建越全」逐字段合并属性、删除 alias；canonical 不存在时整体改名）。

---

## 4. 快速开始

### 4.1 安装

项目用 [uv](https://docs.astral.sh/uv/) 管理（依赖见 `pyproject.toml` / `uv.lock`，要求 Python ≥ 3.11）。

```powershell
cd knowledge_extract/sida-agent
uv sync
```

Embedding 走本地 Ollama，首次需拉取模型（模型名以 `.env` 里 `EMBEDDING_MODEL` 为准）：

```powershell
ollama pull nomic-embed-text:latest
```

并确保 Ollama 服务已启动（`EMBEDDING_BASE_URL` 指向它，如 `http://localhost:11434`）。

### 4.2 配置 `.env`

在 `sida-agent/` 下创建 `.env`（仓库里已有 `.gitignore` 忽略它，**本仓库未提供 `.env.example`**）。
所有值均无内置默认，缺项会在启动时报「配置不完整」并列出缺哪几项：

```ini
# 角色一：视觉解析（PDF 页 -> Markdown），填任意 OpenAI 兼容视觉端点
VISION_BASE_URL=http://your-vision-host/v1
VISION_MODEL=your-vision-model
VISION_API_KEY=sk-xxxx

# 角色二：推理（知识抽取 + 问答），填任意 OpenAI 兼容端点
REASONING_BASE_URL=http://your-reasoning-host/v1
REASONING_MODEL=your-reasoning-model
REASONING_API_KEY=sk-xxxx

# Embedding（向量库，本地 Ollama）
EMBEDDING_BASE_URL=http://localhost:11434
EMBEDDING_MODEL=nomic-embed-text:latest
```

> `config.py` 用 `load_dotenv` 读取，**同名系统环境变量优先于 `.env`**。Ollama 直连时
> `trust_env=False`，避免被 Windows 系统代理劫持（否则可能 502）。

### 4.3 最小可跑命令

先建库（把某本 PDF 的第 11–12 页灌入物理库），再单轮问答：

```powershell
# 建库：视觉提取 + 结构化抽取，累积进双库
uv run python main.py --stage build --pdf "L:/vivi/初三/物理/9S合并PDF-完整.pdf" --start-page 11 --end-page 12 --subject physics

# 问答：复用已持久化的双库
uv run python main.py --stage ask --query "讲解可变电路的分析思路"
```

多轮对话 REPL（会话落盘 `output/chat/`，`Ctrl+C` 退出）：

```powershell
uv run python main.py --stage chat                 # 新建会话
uv run python main.py --stage chat --list           # 只列既有会话
uv run python main.py --stage chat --session s-xxxx # 续聊指定会话
```

> `--pdf` 默认值 `L:/vivi/初三/物理/9S合并PDF-完整.pdf` 是开发机路径，换机器请显式传入自己的 PDF。

---

## 5. 命令行参数表

逐条对应 `main.py` 的 `parse_args()`（`ArgumentDefaultsHelpFormatter`，帮助里会显示默认值）。

| 参数 | 类型 / 取值 | 默认值 | 作用 |
|---|---|---|---|
| `--stage` | `all` / `build` / `ask` / `chat` | `all` | `all`=提取+建库+问答；`build`=仅提取并累加进双库；`ask`=仅复用已持久化双库问答；`chat`=多轮对话 REPL |
| `--pdf` | str | `L:/vivi/初三/物理/9S合并PDF-完整.pdf` | 教材 PDF 路径（build/all 使用） |
| `--book` | str（metavar 教材名） | `None` | 该 PDF 的教材显示名，用于答案「收录于《教材名》」来源标注；缺省取 PDF 文件名去扩展名；重复登记同一 `--pdf` 即改名覆盖 |
| `--start-page` | int | `11` | 起始页码（从 1 计） |
| `--end-page` | int | `12` | 结束页码（含），超出总页数自动截断 |
| `--subject` | `physics` / `chemistry` / `math` | `physics` | 学科；接受中文 / 拼音别名，`_subject_choice` 经 `normalize_subject` 归一化，非法值报命令行错误 |
| `--query` | str | `请帮我系统讲解可变电路的分析思路，并用具体的典型例题带我推导一遍` | 学生提问（ask/all 使用） |
| `--session` | str（metavar ID） | `None` | chat：进入 / 续聊指定会话（`thread_id`，见 `--list`）；缺省新建会话 |
| `--list` | flag（`dest=chat_list`） | `False` | 列出既有会话清单后退出（不进入对话） |
| `--export` | str（`dest=chat_export`，metavar ID） | `None` | 把指定会话导出为 Markdown 后退出（不进入对话） |
| `--max-chars` | int | `6000` | 知识抽取单子块字符预算（build/all）：超过即自动切块增量抽取 |
| `--max-chunks` | int（metavar N） | `None` | 本次建库最多处理 N 个未命中缓存的新子块（缓存命中不占额度），达上限即停、重跑续跑 |
| `--max-new-calls` | int（metavar N） | `None` | 本次视觉提取最多新调用 N 次（已缓存页不占额度），达上限即停、重跑续跑（控视觉模型成本） |
| `--yes` | flag | `False` | 跳过建库前的规模预估确认（脚本 / 夜间批量自动放行） |

> `--list` / `--export` 在 `main()` 里**先于 `--stage` 分发**处理，命中即执行并退出。
> 不带任何参数运行 = 全部默认值（等价旧版硬编码流水线，便于快速回归）。

---

## 6. 目录结构与关键文件职责

```
sida-agent/
├── main.py                    # CLI 入口：argparse、stage 分发、规模预估/确认、TokenMeter、chat REPL、答案落盘
├── config.py                  # LLM 配置：.env 读取、get_llm/get_vision_llm/get_reasoning_llm/get_embedding_model
├── logger.py                  # 日志：控制台(INFO+) + 文件 output/sida_agent.log(DEBUG+) 双通道
├── pdf_processor.py           # 阶段①：PDF 页 → Markdown（视觉提取、逐页缓存、重试/超时、_fix_math、_pdf_id）
├── ingestion.py               # 阶段②：分块 + 两批抽取 + 滚动上下文 + 合并写库 + 审计 + 双库落盘（核心）
├── chat_session.py            # chat 会话后端：SqliteSaver、会话清单/快照/导出 Markdown
├── migrate_book_sources.py    # 一次性迁移脚本：为既有图谱回填 sources + 注册 PdfSource（零 LLM，硬编码历史 pdf_id）
├── agent/
│   ├── state.py               # LangGraph 状态定义（CircuitAgentState）
│   └── workflow.py            # 阶段③：问答工作流（意图判定/图谱检索/回表/生成/搜题/闲聊/上下文压缩）
├── storage/
│   ├── graph_store.py         # ScienceGraphStore：node_key 规则、增删边、get_subgraph/get_entity_subgraph/章节聚合、实体+同族模糊解析、度数族匹配、概念级下钻+相关性截断、合并、save/load
│   └── vector_store.py        # get_vector_store：Chroma 持久化封装
├── pyproject.toml             # 依赖与 Python 版本声明
├── uv.lock                    # 锁定依赖版本
├── .env                       # 模型服务配置（被 .gitignore 忽略，不入库）
├── .python-version            # uv 使用的 Python 版本（3.11）
└── output/                    # 运行产物（被 .gitignore 忽略）
    ├── pdf_extract/{pdf_id}/p{页码}_v2.md   # 视觉逐页缓存
    ├── extract_cache/{key}.json             # 抽取结果缓存
    ├── vector_db/                           # Chroma 持久化向量库
    ├── knowledge_graph.json                 # 图谱 JSON 快照
    ├── answers/answer_{时间}_{学科}.md       # 单轮问答讲解
    ├── chat/checkpoints.sqlite              # chat 会话检查点
    ├── chat/exports/                        # chat 会话导出
    └── sida_agent.log                       # 全量 DEBUG 日志
```

### 关键第三方依赖（`pyproject.toml`，Python ≥ 3.11）

| 依赖 | 用途 |
|---|---|
| `pymupdf` | PDF 页面渲染成 PNG 供视觉模型 |
| `langchain-openai` | 经 OpenAI 兼容端点调用视觉 / 推理模型（`ChatOpenAI`） |
| `langchain-ollama` | 本地 Ollama 嵌入模型（`OllamaEmbeddings`） |
| `langchain-chroma` / `chromadb` | 向量库持久化与检索 |
| `langgraph` / `langgraph-checkpoint-sqlite` | 问答工作流状态机 + chat 会话检查点 |
| `networkx` | 知识图谱（`DiGraph` + node_link JSON 读写） |
| `langchain-core` / `openai` / `pydantic` | 消息 / 客户端 / 配置类型 |
| `python-dotenv` | 读取 `.env` |

---

## 7. 已知局限 / 待优化项

以下均来自本次代码走查读到的实际实现边界，非推测：

- **`--pdf` 默认值是开发机绝对路径**（`L:/vivi/...`），换机器不带 `--pdf` 会直接
  `FileNotFoundError`。
- **无自动化测试 / 冒烟脚本**：仓库内没有任何 `test_*.py` 或 smoke 脚本，正确性依赖
  `output/sida_agent.log` 与人工核对。
- **`TokenMeter` 只统计返回 usage 的调用**：`add()` 在 prompt 与 completion 都为 0 时
  直接 `return`，若某后端不回传 usage，则该次不计入成本统计（`report` 会显示「无实际模型调用」）。
- **规模预估对未提取页是估算**：未缓存页用平均页长占位预演切块，`_estimate_build` docstring
  已注明「实际块数可能略有出入」，真实消耗以结束时 `TokenMeter` 为准。
- **图谱每类关联实体默认截断 top-8**（`_DEFAULT_MAX_PER_KIND`）：命中「枢纽概念」
  （如日志里 `欧姆定律` / `串联电路` 关联上百条例题）时，回表取原文的页数随之减少，
  可能漏掉部分例题。截断前已按锚点 token 相关性排序（见 3.9），无关实体先被丢弃；
  但目标实体名与锚点完全无共享 token 时（如抽取时度数写法漂移），仍可能被截掉。
- **`_audit_graph` 疑似重复检测有假阳性**：`difflib` 名称相似度会把语义相反的近义词判为重复
  （日志实例：`纯电阻电路 ≈ 非纯电阻电路 (0.91)`），且只报告不自动合并，需人工 `merge_concepts`。
- **空壳概念节点仍会存在**：`prerequisites` 前置引用会主动建无描述 Concept 占位（设计上允许），
  审计会列出但不消除。
- **`migrate_book_sources.py` 硬编码了两个历史 `pdf_id` → 教材名**（`3c62fbd6...`、`8babbb8e...`），
  是一次性迁移脚本，不是常规功能，换数据集不适用。
- **多轮对话预算按「字符」粗估**（`_CHAT_HISTORY_BUDGET_CHARS=12000`），非 token 口径，
  中英混排下与实际上下文窗口占用会有偏差。

### 待确认清单（代码无法判断业务原因，不下结论）

- `SUBJECT_ANSWER_GUIDE` / 视觉 `PROMPT` 中若干提示词措辞的教学取舍依据，代码未注释原因。
- `_EXTRACT_VERSION`（v2）与 `_EXTRACT_SCHEMA_VERSION`（v3）版本号不同步是否为有意设计
  （两者分别管视觉页缓存与抽取结果缓存，代码未说明为何不统一）。
- `EMBEDDING_BASE_URL` 示例端口在 `.env` 注释里写的是 `11636`（而非常见 `11434`），
  是否为该环境的自定义端口——以用户实际 `.env` 为准。

---

## 8. 常见问题（结合日志典型场景）

日志文件：`output/sida_agent.log`（DEBUG 全量，含时间戳 / 模块:行号）。每次进程启动写一条
「新运行开始」分隔行。

- **启动报「配置不完整：请在 sida-agent/.env 设置 …」**
  → `config.py` 的 `get_llm` / `get_embedding_model` 缺 `*_BASE_URL`/`*_MODEL`/`*_API_KEY`
  或 `EMBEDDING_*`。按报错列出的变量名补齐 `.env` 并重启（`load_dotenv` 只在启动读一次）。

- **Embedding 502 / 连不上**
  → 确认 Ollama 服务在跑、`EMBEDDING_BASE_URL` 端口正确；`config.py` 已用
  `client_kwargs={"trust_env": False}` 绕系统代理，若仍异常检查是否被代理软件拦截。

- **`[workflow.fetch_chunks] 向量库未命中讲义页: physics:Page:...:11`**
  → 该页讲义页切片不在向量库（多为对应页还没 `--stage build` 入库，或 `pdf_id` 变了导致键对不上）。
  先对含该页的区间跑一次 build。

- **`[graph_store] 图谱中不存在概念节点 …（模糊解析亦未命中）` + `概念节点未命中，尝试实体锚点定位`**
  → 意图 LLM 提炼的锚点不是概念名，属正常兜底路径。接下来看日志：
  - 出现 `集合名词锚点 … 解析为同族实体` / `非概念锚点 … 命中 N 个实体` → 已被
    实体锚点级救回（见 3.8），正常作答；
  - 四级兜底走完、六区块仍全空 → 库里确实没有（如问了未收录的「地震波」），
    回答按提示词输出「当前教材资料未收录…」，需要扩充建库。

- **提问明明在讲义里（如「两角和公式」），却回答「未收录」**
  → 先查 `output/sida_agent.log` 的 `[workflow.graph_traversal] 命中: 公式 N …`。
  若 `公式 N` 非 0 而回答仍拒答，说明命中判据或输出规范被改坏（见 3.8 ⑤：
  `graph_hit` 必须覆盖六桶，只看 `concept` 会把命中误报成未命中）；
  若 `公式 0` 且日志有 `解析为同族实体: {}`，是锚点与节点名结构上无共享前缀，
  属解析能力边界，考虑补建库或调整 `--subject`。

- **提问带角度数字（如「15°、22.5°三角函数值」「18°三角函数」）**
  → 日志出现 `度数标记 … 命中 N 个 …` 即族匹配生效；概念入口命中但目标公式挂在
  兄弟概念名下时，靠 `_drill_concept_entities` 下钻 + `_capped` 相关性排序捞回（见 3.9）。
  两者都没出现，多半是抽取时实体名根本不含量词度数（写法漂移，如 `tan15°` vs
  「15°角的正切值」），属建库质量问题而非检索问题，需回查抽取缓存。

- **`[graph_store] … 关联 examples 共 94 条，截断至 top-8`**
  → 命中枢纽概念，`get_subgraph` 按 `_DEFAULT_MAX_PER_KIND=8` 截断，讲解只覆盖部分例题，
  属预期防 prompt 膨胀行为，不是错误。

- **`[ingestion] 例题 … 的归属题型 … 不在本批题型中，全局图亦不存在，跳过挂边` / `… 的 TESTS 引用了不存在的概念，跳过` / `extra_relations 端点不是已抽取概念，跳过`**
  → 抽取时模型引用了本批和全局图都不存在的实体名（跨子块命名漂移或模型杜撰）。系统**告警跳过、
  不建幽灵节点**，图谱保持干净；如反复出现可考虑增大 `--max-chars` 让相关概念落在同一子块内抽取。

- **`[ingestion] 审计: … 空壳概念节点` / `疑似重复概念对`**
  → 建库后审计报告。空壳多为 `prerequisites` 前置引用；疑似重复是命名漂移。核对后可用
  `graph_db.merge_concepts(subject, canonical=规范名, alias=冗余名)` 显式合并（会重定向关系并删除别名节点）。

- **非交互终端跑 build 被拒绝**
  → `_confirm_build` 检测到 `not sys.stdin.isatty()` 且预估有新调用时直接报错退出。
  确认规模后加 `--yes`，或先用 `--start-page/--end-page/--max-chunks/--max-new-calls` 缩小本次范围。

- **想清空重来**
  → 删除 `output/vector_db` 与 `output/knowledge_graph.json`（双库）；如需连提取 / 抽取缓存一起清，
  再删 `output/pdf_extract` 与 `output/extract_cache`。注意：删 `output/pdf_extract` 会导致下次重新调用
  视觉模型（更贵）。
