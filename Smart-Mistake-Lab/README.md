# Smart Mistake Lab（智能错题本）

一款基于 AI 的数学错题管理工具。指定本地图片文件夹，程序自动扫描并区分已索引/未索引的错题图片，AI 自动识别题目内容并打上知识点标签，支持标签增删改、按考点筛选和关键词搜索。

## 功能概览

- **📂 目录扫描** — 指定本地图片文件夹，程序自动扫描 jpg / png / gif / webp / bmp 图片
- **🔄 索引状态追踪** — 自动判断每张图片是否已被索引，区分"已索引"和"待索引"
- **🔃 刷新扫描** — 在文件夹中加入新图片后，点击刷新按钮即可发现未索引的新图片
- **🤖 AI 分析** — 点击未索引图片，调用大模型自动提取题目标题、内容复述、知识点标签
- **🏷️ 标签编辑** — 支持标签的**增加、删除、修改**（双击标签进入编辑模式）
- **🔍 错题库检索** — 按知识点标签筛选、按关键词搜索已索引的错题
- **💾 本地数据库** — 图片文件保留在原始目录，元数据存储在 SQLite 数据库中

## 架构概览

```
前端 (React + Vite)              后端 (Python FastAPI)               存储层
┌──────────────────────┐        ┌──────────────────────────┐      ┌──────────┐
│  mistake-notebook    │  HTTP  │  server/server.py        │ SQL  │ SQLite   │
│  .jsx                │◄──────►│  (port 8765)             │─────►│ data.db  │
│                      │ proxy  │  ┌────────────────────┐  │      │          │
│  （无 AI 逻辑）       │        │  │ llm.py (AI 交互)  │  │      │ 图片元数据│
│                      │        │  │ log.py (日志)      │  │      │ 配置信息  │
│                      │        │  └────────────────────┘  │      └──────────┘
│                      │        │  文件系统读取 / 图片服务  │
└──────────────────────┘        └──────────────────────────┘
```

- **后端**统一管理 AI 调用、Prompt 构建、API 鉴权与日志
- **前端**只负责界面展示与用户交互，不含任何 AI 或 API Key 逻辑
- **Vite 代理**将 `/api` 请求转发到后端（开发环境免跨域）

## 技术栈

| 层 | 技术 |
|----|------|
| 前端框架 | React 19 + Vite 7 |
| 图标库 | Lucide React |
| 后端 | Python FastAPI + Uvicorn |
| AI 交互 | Python httpx（`server/llm.py`） |
| 日志 | Python logging + RotatingFileHandler（`server/log.py`） |
| 数据库 | SQLite（通过 Python sqlite3） |
| AI 接口 | 支持图片输入的 OpenAI Chat Completions / Anthropic Messages |

## 快速开始

### 环境要求

- Node.js >= 18, npm >= 9
- Python >= 3.10
- [uv](https://docs.astral.sh/uv/)（Python 包管理器）

### 1. 安装前端依赖

```bash
cd Smart-Mistake-Lab
npm install
```

请确保当前目录就是 Smart-Mistake-Lab；如果在工作区根目录直接执行 npm run dev，npm 会因为找不到 package.json 启动失败。

### 2. 安装后端依赖

```bash
cd server
uv sync
```

此命令会自动创建 `.venv` 虚拟环境并根据 `pyproject.toml` 安装所有依赖。

### 3. 启动后端服务

```bash
# 在 Smart-Mistake-Lab/server 目录下
cd server
uv run python server.py
```

`uv run` 会自动激活虚拟环境并启动服务，后端默认运行在 `http://127.0.0.1:8765`。

### 4. 启动前端开发服务器

```bash
# 在 Smart-Mistake-Lab 目录下（新开一个终端）
cd Smart-Mistake-Lab
npm run dev
```

前端默认运行在 `http://localhost:5173`。

Vite 开发服务器会自动将 `/api` 请求代理到后端 `127.0.0.1:8765`。

### 构建生产版本

```bash
npm run build      # 输出到 dist/
npm run preview    # 本地预览生产构建
```

> 生产部署时需要配置反向代理（如 Nginx）将 `/api` 转发到后端。

## 配置说明

### 图片目录配置

在应用的 **配置** 页面设置本地存放错题图片的文件夹路径，例如：

```
C:\Users\me\Pictures\错题
```

目录配置保存在后端 SQLite 数据库中，重启后依然生效。

### AI 配置

AI 参数由后端服务统一管理，通过 `Smart-Mistake-Lab/` 目录下的 `.env` 文件配置：

```env
AI_API_URL=https://your-openai-compatible-host/v1/chat/completions
AI_MODEL=your-vision-model
AI_API_KEY=sk-your-api-key-here
```

> API Key 仅存于服务端，不会暴露到浏览器端。修改后需重启后端服务 `uv run python server.py`。

说明：

- 本应用的"AI 分析知识点"会向模型发送图片输入，因此所选模型必须支持视觉输入。
- DeepSeek 当前仅支持文本消息，不支持本应用使用的图片分析请求格式，因此不适合作为这里的图片分析模型。
- 如果你使用 Anthropic Messages 接口，请将 URL 配置为 `/v1/messages`，并使用支持图片输入的模型。

### 支持的 AI 接口格式

| 格式 | 鉴权方式 | 适用服务 |
|------|----------|----------|
| OpenAI 兼容 `/v1/chat/completions` | `Authorization: Bearer` | 支持图片输入的 OpenAI 兼容模型服务 |
| Anthropic `/v1/messages` | `x-api-key` + `anthropic-version` | 支持图片输入的 Claude 模型服务 |

程序会根据接口 URL 自动判断请求格式。

## 项目结构

```
Smart-Mistake-Lab/
├── index.html                  # 入口 HTML
├── package.json                # 前端依赖与脚本
├── vite.config.js              # Vite 配置（含 API 代理）
├── .env                        # 环境变量（AI_API_URL / AI_MODEL / AI_API_KEY）
├── mistake-notebook.jsx        # 主应用组件（含所有页面逻辑与样式）
├── src/
│   ├── main.jsx                # React 挂载入口
│   └── App.jsx                 # 组件导出
└── server/
    ├── pyproject.toml           # 项目配置与依赖（uv 管理）
    ├── server.py               # FastAPI 后端服务入口
    ├── db.py                   # SQLite 数据库操作层
    ├── llm.py                  # AI 交互模块（Prompt 管理、API 调用、响应解析）
    ├── log.py                  # 日志模块（控制台 + 轮转文件输出）
    ├── data.db                 # SQLite 数据库（自动创建）
    ├── config.json             # 配置文件（自动创建）
    └── logs/
        └── server.log          # 服务端运行日志（自动创建，10MB 轮转）
```

## 使用说明

### 初次使用

1. 启动后端（`uv run python server.py`）和前端（`npm run dev`）
2. 打开浏览器访问 `http://127.0.0.1:5173` 或 `http://localhost:5173`
3. 进入 **配置** 页面，设置图片存放目录路径并保存
4. 确保 `.env` 文件中已配置 `AI_API_URL`、`AI_MODEL`、`AI_API_KEY`
5. 切换到 **扫描** 页面，点击 **刷新扫描**
6. 点击任意 **待索引** 的图片缩略图
7. 点击 **AI 分析知识点**，等待分析完成
8. 检查/编辑标题、题目复述、知识点标签（**双击标签可编辑**，点击 × 可删除）
9. 点击 **保存索引**，该图片标记为已索引

### 日常使用

1. 将新错题图片放入配置的图片目录
2. 打开应用，进入 **扫描** 页面，点击 **刷新扫描**
3. 新图片会出现在"待索引"区域，点击即可分析
4. 切换到 **错题库** 页面，按标签筛选或关键词搜索已索引的错题
5. 点击错题卡片查看详情，可编辑标签或从索引中移除

### 标签操作

| 操作 | 方式 |
|------|------|
| 添加标签 | 在输入框输入后回车或点击 + 按钮 |
| 修改标签 | 双击标签，或点击编辑按钮 ✎，修改后回车确认 |
| 删除标签 | 点击标签上的 × 按钮 |

