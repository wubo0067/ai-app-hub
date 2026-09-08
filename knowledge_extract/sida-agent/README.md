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
        ──③ LangGraph 问答 Agent──▶ 分层讲解 / 按题目内容搜题 / 多轮对话
              判定学科 + 提问意图（问知识点 / 找题目 / 闲聊）
              ├─ concept：知识点锚点 → 图谱聚合检索（模糊解析锚点 + 每类 top-N 截断）
              │            → 按 (pdf_id, 页码) 回表取讲义页原文 → 生成回答（标注教材来源）
              ├─ find_problem：整页讲义原文检索（逐字命中 + 语义兜底重排）
              │               → 原题完整呈现 + 出处页码 + 简析（详见下节）
              └─ chat：会话记忆 + 上下文压缩 + 跨进程续聊（详见第 4 节 chat 小节）
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
- **预算上限（推理侧）** `--max-chunks N`：单次最多处理 N 个**未命中缓存的新子块**
  （缓存命中不占额度），达到即主动停并提示「重跑同命令续跑」，配合预估分轮灌完整本书。
- **预算上限（视觉侧）** `--max-new-calls N`：单次最多新提取 N 页（已缓存页不占额度）。
  视觉模型按多模态输入通常比推理模型更贵，`extract_pdf_pages_as_markdown` 达到上限即停，
  已完成页已逐页缓存，重跑同命令续跑——与 `--max-chunks` 同一套分批消费模式。
  传入后规模预估/确认只会亮「本批真实会做的量」（被截断的剩余页数单独提示留待续跑），
  不会把整个大区间的全量数字拿出来误导确认。
- **真实 token 统计**（`main.py TokenMeter`）：从响应 `usage_metadata`（回退
  `response_metadata.token_usage`）读取真实用量，视觉 / 推理两路分别透传并在结束时打印，
  服务端不回传 usage 的调用不计入。

### 相关命令行参数

| 参数 | 作用 |
|---|---|
| `--max-chars N` | 单子块字符预算（默认 6000）：输入页超过即自动切块 |
| `--max-chunks N` | 推理抽取侧：本次最多处理 N 个未命中缓存的新子块（缓存命中不占额度），达上限主动停 |
| `--max-new-calls N` | 视觉提取侧：本次最多新提取 N 页（已缓存页不占额度），达上限主动停，重跑续跑 |
| `--yes` | 跳过建库前的规模预估确认（脚本 / 夜间批量自动放行） |

```powershell
# 整本教材分轮增量建库：先预估，每轮只处理 20 个新子块；再跑同命令即续跑（已缓存块不计费）
uv run python main.py --stage build --pdf 整本教材.pdf --start-page 13 --end-page 320 --subject math --max-chunks 20

# 视觉提取也分批：本轮新提取页与推理子块各限 20，剩余页/块下次重跑同命令续跑
uv run python main.py --stage build --pdf 整本教材.pdf --start-page 13 --end-page 320 --subject math --max-new-calls 20 --max-chunks 20
```

## 3. 问答意图：知识点讲解 / 按题目内容搜题 / 闲聊

问答入口会先由意图判定节点把提问分成三类（`agent/workflow.py`），再路由到不同链路：

| 意图 | 典型提问 | 链路 |
|---|---|---|
| `concept`（问知识点） | 「请讲解可变电路的分析思路」 | 知识点锚点 → 图谱聚合检索 → 按页回表讲义原文 → 分层讲解 |
| `find_problem`（找题目） | 「我想查询一道题，内容包含'甲、乙两瓶等量煤油中'」 | 整页讲义切片原文检索 → 原题完整呈现 + 出处页码 + 简析 |
| `offtopic`（闲聊 / 寒暄） | 「谢谢」「你好」「你是谁」 | 不触发任何检索 → `respond_chitchat` 轻量直答并引导回学习 |

> 三种意图由同一判定节点输出（`analyze_intent` 只出一行 JSON）。chat 模式会额外把
> 每轮提问与回答累积成 `messages` 并注入【对话背景】帮助指代消解（机制详见第 4 节
> 「chat 多轮对话模式」），ask 单轮则无历史，二者判定 prompt 相同。

**为什么需要 find_problem 链路**：concept 链路靠「知识点锚点 → 图谱」定位内容，学生若
用题目原文片段找题而非问知识点（如题目挂「焦耳定律/串联分压」名下、锚点却被解析成「比热容」），
或目标知识点实体不在图谱中时，图谱检索会整体落空。find_problem 链路绕开图谱，直接在
**整页讲义切片**（`{subject}:Page:{pdf_id}:{页码}`）上做原文检索。

**两级检索策略**（`search_problems_node`，实测均能稳定命中目标页）：

1. **逐字命中**：Chroma `where_document={"$contains": 题目特征文本}` 精确过滤
   （中文子串匹配可用），命中即返回，零成本零误差；
2. **语义兜底 + 二元组重排**：原文有改写/跨行断字导致逐字不中时，向量粗召回 top-10，
   再按「查询字符二元组（bigram）在页面文本中的重合度」降序、向量距离升序重排取
   top-3——纯向量距离对「短引文 vs 长页面」区分度差（目标页曾排 8/8），bigram 重合度
   能把目标页拉回第一（实测 0.86 vs 其余 0.29）。

**输出约定**：命中页的整页讲义原样交给讲解 LLM，题目（题号/题干/选项）完整原样呈现、
不截断不改写，标注「（见教材第 X 页）」；多页相似时列出候选让学生确认；对不上时如实
说明不编造。注意返回的是**整页切片**，同页其他题目也会一并出现，属预期设计。

前提：目标 PDF 的讲义页切片须已入向量库（对该 PDF 跑过 `--stage build` 即可）。

```powershell
# 按题目内容找题（可带请求前缀，意图节点会自动提炼 search_text）
uv run python main.py --stage ask --query "我想查询一道题，内容包含'甲、乙两瓶等量煤油中'"
```

## 4. 常用命令

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
# 视觉提取同样分批：每轮新提取页上限 20（控多模态视觉成本），重跑续跑
uv run python main.py --stage build --pdf 整本教材.pdf --start-page 13 --end-page 320 --subject math --max-new-calls 20
uv run python main.py --stage ask   --query "请讲解可变电路的分析思路"                                # 仅问答（知识点讲解），复用已持久化双库
uv run python main.py --stage ask   --query "我想查询一道题，内容包含'甲、乙两瓶等量煤油中'"            # 按题目内容找题（find_problem，见上节）
uv run python main.py --stage chat                                                                     # 多轮对话 REPL（新开会话）
uv run python main.py --stage chat --session s-018522af7119                                            # 续聊指定会话（跨进程恢复历史）
uv run python main.py --stage chat --list                                                              # 列出历史会话
uv run python main.py --stage chat --export s-018522af7119                                             # 把会话导出为 Markdown
uv run python main.py                                                                                  # 不带参数 = 内置默认示例
```

### chat 多轮对话模式（`--stage chat`）

`--stage ask` 是"每问一次跑一轮无状态问答"；`--stage chat` 在同一会话内可连续追问
（知识点讲解 / 按内容找题 / 闲聊），并把对话按会话 ID（`thread_id`）持久化，可跨进程续聊。

**功能速览**

- **会话持久化**：每轮提问与回答作为消息追加，由 `langgraph-checkpoint-sqlite` 按
  `thread_id` 写入 `output/chat/checkpoints.sqlite`（新会话自动分配，如 `s-018522af7119`）。
- **REPL 命令**：`/exit` 退出 | `/new` 开新会话 | `/list` 列历史会话 | `/session <id>` 切换续聊 |
  `/export` 导出当前会话 Markdown | `/help` 帮助（`--stage chat --list` / `--export <id>`
  也可不经 REPL 直接调用）。
- **闲聊兜底**：与学科无关的话（`offtopic`）直接简短闲聊回复，不触发图谱 / 向量检索。
- **每轮存档**：讲解同时按 ask 同款格式存 `output/answers/answer_{时间戳}_{学科}.md`。

> 注：单轮 `--stage ask` 行为与历史完全一致（不携带 `messages`、不写会话库），可混用；
> 两种模式共用同一张编译图，chat = 编译时挂 `checkpointer` + 每轮多传 `messages`。

#### chat memory 是什么（记忆机制）

对话记忆分两层，都随会话 `thread_id` 存于 SQLite checkpoint：

| 层 | 状态字段 | 内容 | 作用 |
|---|---|---|---|
| 短期记忆 | `messages` | 最近若干轮 Human/AI 消息原文（保留预算见下节） | 可逐字引用的对话窗口 |
| 长期记忆 | `history_summary` | 被截断的更早轮次压缩出的中文摘要（覆盖式，不随轮累积） | 超窗话题仍可指代 |

- `messages` 是 LangGraph 累积通道（`Annotated[list[AnyMessage], add_messages]`，见
  `agent/state.py`）：每轮提问以 `HumanMessage` 压入，生成节点把回答以 `AIMessage` 写回
  （`agent/workflow.py` 各生成节点的返回值带 `messages` 字段）；checkpoint 每轮结束后把
  整图状态快照（含 messages / history_summary / query / final_answer）落盘。
- **恢复原理**：跨进程 / 崩溃续聊 = 用同一 `thread_id` 打开 saver → 取该会话最新
  checkpoint 的 `channel_values` 重建状态 → 继续跑同一张图，无需手工拼历史。
- **与知识库记忆的区别**：Chroma / 知识图谱记的是"教材内容"（跨会话恒定，所有人共用）；
  checkpoint 记的是"这个会话聊了什么"（按 thread_id 隔离）。当前**不做**跨会话的
  用户画像 / 长期偏好记忆（如需要按学生持久化画像，可在此结构上扩展）。
- 三层 LLM 分工（`create_circuit_agent`）：意图判定（低温 / 128 token / 关思考，只出
  一行 JSON）、最终讲解（默认思考 / 大 token 预算）、上下文摘要 `summary_llm`
  （低温 / `_CHAT_SUMMARY_MAX_TOKENS=600` / 关思考）——摘要实例独立，不挤占讲解质量。

#### 超过 LLM context limit 怎么解决（`manage_context` 节点）

图入口先经 `manage_context`（在意图判定**之前**），把喂给 LLM 的输入与总轮数解耦，
保证单轮输入恒为有界：

1. 把 `messages` 全部正文长度求和；未超过预算
   `_CHAT_HISTORY_BUDGET_CHARS = 12000`（字符）时什么都不做；
2. 超预算：从末尾往前保留消息直到预算，**保底保留最近 1 条（本轮提问）**，其余判为丢弃；
3. 丢弃的消息渲染成「学生：… / 老师：…」交给 `summary_llm` 压缩；prompt 携带**旧摘要**
   做**增量更新**（只增补 / 修正新要点），输出上限 600 token、每次覆盖写回
   `history_summary`，避免摘要随轮数无限膨胀；
4. 节点返回 `RemoveMessage` 列表删除被丢弃的旧消息 + 覆盖式新摘要；摘要 LLM 异常时
   降级为"只删消息、保留旧摘要"（`log.warning` 留痕），不阻断主链路。

随后每轮的意图判定 / 生成 / 闲聊 prompt 都注入【对话背景】= `history_summary` + 最近
≤3000 字符的对话窗口（`_recent_context`，且去掉本轮提问本身），于是：

- 隔轮 / 跨进程都能指代前文（"那第二题呢""刚才说的适用条件是什么"）；
- 单次 LLM 输入 ≈ 有界背景 + 本轮检索资料 + 本轮提问 + 系统指令 → 会话可无限长；
- 代价：被摘要的消息不再逐字保留（导出 md 会在头部标注"更早对话摘要"）。

预算常量（`agent/workflow.py` 顶部）：`_CHAT_HISTORY_BUDGET_CHARS=12000`（保留窗口）、
`_CHAT_SUMMARY_MAX_TOKENS=600`（单次摘要）、`_recent_context max_chars=3000`（注入窗口），
均可按所选模型窗口大小调整。

#### 一轮对话的完整数据流

```
你 > 提问
 └─ inputs = {"query": 提问, "messages": [HumanMessage(提问)]}
    config = {"configurable": {"thread_id": 会话ID}}      # 同一 id 即同一会话
    agent.stream(inputs, config=config, stream_mode=["messages", "values"])
     ├─ manage_context    超预算才截断 + 增量摘要（见上）
     ├─ analyze_intent    注入背景 → JSON：subject / intent / concept / search_text
     ├─ 路由 route_by_intent
     │    ├─ concept      → graph_traversal → fetch_chunks → generate_response
     │    ├─ find_problem → search_problems → generate_problem_response
     │    └─ offtopic     → respond_chitchat
     ├─ 生成节点用 llm.stream 流式产出（_stream_answer 内部拼回全文）
     └─ 返回 AIMessage 追加进 messages → checkpointer 落盘 → 等待下一轮
```

#### 流式打印与存档（CLI 层，`main.py::_run_chat_repl`）

- `stream_mode=["messages", "values"]` 双流并行：
  - `messages` 流：生成节点 LLM 的逐 token 增量，按 `meta["langgraph_node"]` 过滤出
    `generate_response` / `generate_problem_response` / `respond_chitchat` 三个生成节点，
    delta 增量打印（`text.startswith(printed)` 去重，兼容部分后端"先增量块、再完整块"
    的重复推送），实现"边生成边显示"；
  - `values` 流：每个节点执行后的完整状态快照，取最后一份作为该轮 `final_answer`，
    归一公式定界符后存 `output/answers/answer_*.md`。
- 单轮 ask 与 chat 共用同一套流式打印逻辑（`main.py` 两处调用同一模式）。

#### 导出与会话管理

- `chat_session.py`：`open_saver()` 生命周期内保持单连接；`list_sessions()` 直接读
  sqlite 统计（thread_id / 更新时间 / 轮数 / 首问）；`export_session_md` 读该会话最新
  checkpoint 的 `messages` 通道按轮渲染——有摘要先列"更早对话摘要"，末尾悬空提问标注
  "（该轮暂无回答）"，`\[…\]` / `\(…\)` 公式定界符归一为 `$$…$$` / `$…$`；
  产物 `output/chat/exports/session_{id}_{ts}.md`。
- 数据位置与清理：全部会话在同一 sqlite 文件（thread_id 维度），删除 `output/chat/`
  即清空所有会话历史，不影响知识库双库与 `output/answers/`。

### 测试 / 自检

项目暂无正式测试套件，常用冒烟方式：

```powershell
# 全模块导入冒烟
uv run python -c "import main, ingestion, pdf_processor, config, agent.workflow, storage.graph_store, storage.vector_store; print('ALL IMPORTS OK')"

# 查看运行日志（控制台 INFO，文件 DEBUG）
Get-Content output\sida_agent.log -Tail 50
```

## 5. 重要目录与文件

| 路径 | 重要度 | 说明 |
|---|---|---|
| `main.py` | ★★★ | 流水线入口：提取 → 建库 → 问答 / 多轮对话；`--stage/--pdf/--start-page/--end-page/--subject/--max-chars/--max-chunks/--max-new-calls/--yes/--query` 参数化；`--stage chat` 多轮对话 REPL（`--session <id>` 续聊 / `--list` 列会话 / `--export <id>` 导出会话 md）；建库前打印规模预估并确认（`--yes` 跳过，`--max-new-calls` 截断后预估只亮本批真实量），结束打印两路真实 token 消耗，换材料无需改源码 |
| `chat_session.py` | ★★ | chat 会话后端：SqliteSaver 连接与生命周期（`open_saver`）、会话清单（`list_sessions`）、最新 checkpoint 快照（`session_snapshot`）、会话导出 Markdown（`export_session_md`）；数据落 `output/chat/checkpoints.sqlite` |
| `config.py` | ★★★ | 统一 LLM/Embedding 工厂；`.env` 中 base_url/key/model 在此生效 |
| `ingestion.py` | ★★★ | 核心：自动切子块（`_split_into_chunks`）、滚动上下文注入（`_gather_known_context`）、两批串行抽取 prompt、逐子块抽取缓存与落盘、双库写入编排、幽灵节点/疑似重复审计 |
| `agent/workflow.py` | ★★★ | LangGraph 问答工作流：学科 + 提问意图（问知识点 / 找题目 / 闲聊）判定 → concept 走图谱检索、按页码回表讲义页生成；find_problem 走整页讲义两级原文检索（逐字命中 + 语义兜底 bigram 重排）原题呈现；chat 模式 `manage_context` 上下文截断 + 增量摘要、`respond_chitchat` 闲聊直答、生成节点流式产出并写回会话历史（意图/摘要/讲解三 LLM 分调优、答案来源标注） |
| `storage/graph_store.py` | ★★★ | `ScienceGraphStore` 图谱存储、`get_subgraph` 聚合检索（每类实体 top-N 截断）、概念锚点模糊解析、疑似重复概念合并（`merge_concepts`/`find_similar_concept`） |
| `pdf_processor.py` | ★★ | PDF 页渲染 + 视觉模型提取 Markdown，逐页缓存于 `output/pdf_extract/` |
| `storage/vector_store.py` | ★★ | Chroma 向量库初始化（collection `science_kb`，落盘 `output/vector_db/`） |
| `agent/state.py` | ★ | Agent 状态 TypedDict：query / target_subject / 检索结果等 + chat 字段 `messages`（`add_messages` 累积通道）与 `history_summary`（截断旧对话的覆盖式摘要） |
| `logger.py` | ★ | 控制台 + `output/sida_agent.log` 双通道日志 |
| `.env` | ★★ | 模型服务配置（不入库）；`.env` 缺失或 key 为空时启动会给出指引报错 |
| `output/` | — | 运行产物：日志、PDF 提取缓存（`pdf_extract/{pdf_id}/pXXXX_{ver}.md`）、抽取缓存（`extract_cache/{hash}.json`）、向量库（`vector_db/`）、图谱（`knowledge_graph.json`）、chat 会话库与导出（`chat/checkpoints.sqlite`、`chat/exports/`） |

## 6. 其它说明

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
- 清空 chat 会话历史：删除 `output/chat/`（`checkpoints.sqlite` + `exports/`）即可；
  知识库双库与 `output/answers/` 单轮讲解存档不受影响。
- 向量写入以 `metadata.id` 为键幂等 upsert，重复重建不会在 Chroma 中累积重复切片；
  修改抽取 schema 后请递增 `ingestion._EXTRACT_SCHEMA_VERSION` 使旧抽取缓存失效，
  修改 PDF 提取 PROMPT/渲染参数/后处理后请递增 `pdf_processor._EXTRACT_VERSION`
  使旧页缓存失效。
- 图谱检索截断：`get_subgraph` 对公式/实验/题型/方法/例题每类默认返回 top-8
  （`storage/graph_store._DEFAULT_MAX_PER_KIND`，调用时传 `max_per_kind=None` 关闭），
  命中「枢纽概念」（关联几十条实体）时防止撑爆下游 prompt；examples 截断会连带
  减少按页回表的讲义页数。
- 多 LLM 分调优：`agent/workflow.py` 的意图判定、上下文摘要、最终讲解各用独立的
  `get_reasoning_llm` 实例——判定走低温 / 小 `max_tokens` / 关思考（只输出一行 JSON，
  短平快）；摘要（chat 上下文管理用）同样低温 / 600 token / 关思考；讲解保留默认思考
  与大 token 预算（长输出），三者参数互不干扰。
- 答案可追溯性：生成 prompt 注入「本次检索命中情况」（图谱命中与否 + 讲义命中页码），
  输出规范要求模型对取自例题原文的内容标注「（见教材第 X 页）」、取自图谱各区块的标注
  「（教材知识点，图谱收录）」（图谱实体抽取时不记页码，只能到图谱粒度）、自行补充的
  学科知识另起「【补充说明·教材未涉及】」段；仅当图谱与讲义双双未命中时，才在正文开头
  声明为通用讲解——避免无教材支撑的内容以同等自信误导学生 / 家长。
