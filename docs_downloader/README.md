# 网站文件下载爬虫 使用说明

一个基于 Python3 的爬虫工具，可以登录指定网站，递归爬取网页，下载指定类型的文件（如 docx、pdf 等），并按照网站原有的目录层级在本地保存。项目已配置为 uv 虚拟环境项目，方便打包后到其他机器一键还原运行。

### 项目文件说明

| 文件 | 说明 |
|---|---|
| `web_downloader.py` | 主程序 |
| `config.ini` | 配置文件（网址、账号密码、下载目录等，**首次使用前必须修改**） |
| `pyproject.toml` | uv 项目配置文件（项目信息、依赖声明） |
| `uv.lock` | uv 依赖锁文件（锁定精确版本号，保证多机运行环境一致） |
| `.python-version` | 项目指定使用的 Python 版本，uv 会据此自动下载对应版本 |
| `requirements.txt` | 传统 pip 方式的依赖列表（不用 uv 时使用） |
| `README.md` | 本说明文档 |

打包分发给其他人 / 拷贝到其他机器时，只需要带上以上文件即可（**不需要**带 `.venv` 目录和 `crawler.log`、`downloads/` 等运行时产生的文件）。

---

## 一、环境准备（使用 uv，推荐）

本项目已配置为 [uv](https://docs.astral.sh/uv/) 虚拟环境项目，包含 `pyproject.toml` 和 `uv.lock` 锁文件，可以做到"在一台机器上锁定依赖版本，打包后拿到另一台机器上一键还原运行"，不需要再手动 `pip install`，也不用担心不同机器上依赖版本不一致的问题。

### 1. 安装 uv

如果目标机器还没有装 uv，任选一种方式安装：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 或者如果机器上已有 pip
pip install uv
```

安装完成后检查：

```bash
uv --version
```

### 2. 还原虚拟环境并安装依赖

进入项目目录（包含 `pyproject.toml`、`uv.lock` 的目录），执行：

```bash
uv sync
```

这一步会自动：
- 按 `.python-version` 指定的版本下载/使用对应的 Python 解释器（无需你本机预装该版本）
- 在项目目录下创建 `.venv` 虚拟环境
- 严格按照 `uv.lock` 中锁定的版本号安装 `requests`、`beautifulsoup4`、`lxml` 等依赖

`uv sync` 只需要在**第一次**运行前执行一次，或者依赖有更新时再执行。

### 3. 运行程序

不需要手动激活虚拟环境，直接用 `uv run` 运行即可，uv 会自动使用项目自己的虚拟环境：

```bash
uv run python web_downloader.py config.ini
```

### 4. 打包到其他机器运行

把整个项目目录（包含 `pyproject.toml`、`uv.lock`、`web_downloader.py`、`config.ini`、`README.md`，**不需要**带上 `.venv` 目录）拷贝或压缩发送到目标机器，在目标机器上：

```bash
# 目标机器只要装好 uv 即可
uv sync
uv run python web_downloader.py config.ini
```

`uv.lock` 保证了目标机器安装的依赖版本和你本机开发测试时完全一致，避免"我这里能跑，别处跑不了"的问题。`.venv` 目录本身不需要拷贝（体积较大，且和系统架构相关），每台机器用 `uv sync` 各自还原即可。

---

## 一点五、（备选）不使用 uv，用传统 pip 方式运行

如果目标机器不方便安装 uv，也可以用传统方式：

### 1. 安装 Python3

需要 Python 3.9 及以上版本。可在命令行执行以下命令检查：

```bash
python3 --version
```

### 2. 安装依赖库

进入程序所在目录，执行：

```bash
pip3 install -r requirements.txt
```

如果安装很慢，可以使用国内镜像源：

```bash
pip3 install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

这种方式下，运行程序改用：

```bash
python3 web_downloader.py config.ini
```

---

## 二、配置文件说明（config.ini）

使用前，请先打开 `config.ini` 文件，根据你要爬取的网站，修改以下配置项：

### [site] 网址相关

| 配置项 | 说明 |
|---|---|
| `start_url` | 爬虫开始爬取的页面地址，一般填放文件列表的目录页 |
| `base_url` | 网站的根地址（如 `https://example.com`），用于限制爬虫只在该网站内爬取，防止爬到外部网站 |

### [login] 登录相关

| 配置项 | 说明 |
|---|---|
| `enable_login` | 是否需要登录，`true` 或 `false` |
| `login_url` | 登录页面地址 |
| `login_post_url` | 登录表单实际提交的地址（如果和登录页面地址一样，可以填一样的） |
| `username_field` | 登录表单中"用户名"输入框的 name 属性（见下方"如何查找表单字段名"） |
| `password_field` | 登录表单中"密码"输入框的 name 属性 |
| `username` | 你的登录账号 |
| `password` | 你的登录密码 |
| `extra_fields` | 表单里其他需要一起提交的固定字段（一般不用填，程序会自动尝试提取隐藏字段） |

#### 如何查找表单字段名（username_field / password_field）

1. 用 Chrome/Edge 浏览器打开网站登录页面
2. 按 `F12` 打开开发者工具，切换到 "Elements"（元素）标签
3. 找到用户名输入框，通常形如：
   ```html
   <input type="text" name="username" ...>
   ```
   这里的 `name="username"` 中的 `username` 就是要填入配置文件 `username_field` 的值
4. 同样方法找到密码框的 `name` 属性

> 注意：不同网站的字段名可能是 `user`、`account`、`loginName`、`pwd`、`password` 等，请务必以实际页面为准。

### [download] 下载相关

| 配置项 | 说明 |
|---|---|
| `extensions` | 要下载的文件后缀，多个用英文逗号分隔，例如 `docx,pdf,xlsx` |
| `save_dir` | 下载文件保存的本地根目录，可以填相对路径（如 `./downloads`）或绝对路径 |

### [crawler] 爬虫行为相关

| 配置项 | 说明 |
|---|---|
| `max_depth` | 最大爬取深度，防止爬虫无限递归。数字越大，爬取范围越广，耗时也越长 |
| `delay_seconds` | 每次请求之间的等待时间（秒），避免过快请求给网站造成压力或被封 IP |
| `timeout` | 每次网络请求的超时时间（秒） |
| `skip_existing` | 如果本地已存在同名文件是否跳过下载，`true` 或 `false` |
| `user_agent` | 爬虫伪装的浏览器标识，一般无需修改 |

---

## 三、运行程序

配置文件填写完成后，在命令行中执行（uv 方式）：

```bash
uv run python web_downloader.py config.ini
```

如果配置文件就叫 `config.ini` 且和脚本在同一目录，也可以省略参数：

```bash
uv run python web_downloader.py
```

（如果没有用 uv，改用 `python3 web_downloader.py config.ini` 即可，见上文"备选"方式。）

程序运行时会在命令行实时打印爬取和下载进度，同时会生成一个 `crawler.log` 日志文件，记录所有操作细节，方便排查问题。

---

## 四、下载结果说明

- 程序会按照网站上文件的 URL 路径，在 `save_dir` 指定的目录下自动创建相同层级的子目录。

  例如网站上文件地址为：
  ```
  https://example.com/documents/2024/数学/期末试卷.docx
  ```
  本地保存目录为 `./downloads`，则最终会保存到：
  ```
  ./downloads/documents/2024/数学/期末试卷.docx
  ```

- 中文文件名会自动从 URL 编码还原成正常中文显示。

---

## 五、常见问题

**1. 提示登录后仍然爬不到内容 / 提示未授权？**

部分网站的登录验证比较复杂（例如需要验证码、短信验证、或使用 JavaScript 动态生成加密参数），这类网站单纯用 `requests` 模拟表单提交可能无法登录成功。如果遇到这种情况，可以：
- 检查 `login_post_url`、`username_field`、`password_field` 是否填写正确
- 查看 `crawler.log` 中登录请求返回的状态码
- 如果网站有验证码或复杂加密逻辑，需要额外定制登录逻辑（可以把登录页面情况反馈给我，我再帮你调整代码）

**2. 下载的文件数量比预期少？**

- 检查 `max_depth` 是否设置太小，导致爬虫还没爬到深层目录就停止了
- 检查网站文件链接是否是通过 JavaScript 动态加载的（比如点击后才异步请求数据）。这种情况需要用 Selenium 等工具模拟浏览器行为，当前脚本暂不支持，如有需要可以告诉我另行开发。

**3. 报错 `requests.exceptions.SSLError`？**

一般是网站证书问题，如果是内部可信网站，可以在代码中关闭证书验证（不建议用于公网不明网站）。如遇到此问题，请反馈给我，我可以帮你加一个配置开关。

**4. 想要限速更慢、爬取范围更小怎么设置？**

调大 `delay_seconds`（如 1~2 秒），调小 `max_depth`（如 2~3）即可。

---

## 六、注意事项

- 请仅爬取你有权限访问、并遵守目标网站服务条款的内容，不要用于未经授权的批量抓取。
- 如果目标网站有 `robots.txt` 限制或明确禁止爬虫，请遵守相关规定。
- 建议先用较小的 `max_depth`（如 1 或 2）小范围测试，确认能正常登录、正常下载后，再放开范围正式爬取。
