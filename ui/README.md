# VMCore 分析管理台

独立的 FastAPI 管理后台，前端静态文件由自身服务提供。支持两种执行方式：默认脚本模式调用 `bash /data/analytics_vmcore.sh <vmcore 父目录相对扫描根目录的路径>`，例如 `/data/vmcore/127.0.0.1/file/vmcore` 传入 `127.0.0.1/file`；也可选 Agent HTTP 模式，通过 `/health`、`/analyze/stream` 调用已有服务。管理台代码始终位于 `ui/`，与 Agent 分开启动部署。

## 目录

```
ui/
  app.py             HTTP 路由、登录、队列、Agent 流消费
  analysis.py        报告字段提取、崩溃 ID、统计口径
  discovery.py       多层目录扫描、进度事件和文件稳定性检查
  metadata.py        crash sys 识别及 vmcore 同目录 osinfo.txt 校验缓存
  script_reports.py  脚本产物比对、vmcore 路径核对及审计报告配对
  storage.py         原子 JSON / Markdown 写入
  static/            自带前端
  tests/             存储、重启、导入、统计及 API 集成测试
  config.example.json
  run.py
  data/              首次运行自动创建；备份整个目录
    config.json, tasks.json, cases.json, reviews.json, audit_log.json
    reports/*.md, audits/*.md, logs/*.log
```

## 启动

需要 Python 3.11+。脚本模式要求管理台所在主机有 `bash`、`/data/analytics_vmcore.sh`，且进程可读取 `/data/vmcore`、可读取脚本的报告输出目录。脚本内部需要的 Agent 服务按该脚本的部署要求独立启动。若切换 Agent HTTP 模式，两个服务需能访问 vmcore、dmesg、vmlinux 及调试符号的同一绝对路径。

```bash
cd ui
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export UI_ADMIN_PASSWORD='换成内部强密码'
export UI_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export UI_DATA_DIR=/srv/vmcore-ui-data
export UI_HOST=127.0.0.1
export UI_PORT=8080
python run.py
```

Windows PowerShell 中把 `source`/`export` 换成 `.venv\Scripts\Activate.ps1` 和 `$env:UI_ADMIN_PASSWORD=...` 等赋值。首次启动将 `config.example.json` 复制为 `data/config.json` 的内容。登录后在“设置”中填写本机真实的扫描目录、符号目录和 Agent URL。默认监听本机 `127.0.0.1:8080`，生产环境通过带 TLS 的内部反向代理访问；设置 `UI_SECURE_COOKIE=1` 后浏览器仅通过 HTTPS 发送登录 Cookie。只运行一个 Uvicorn worker 和一个分析任务，不要把同一 `data` 目录挂给多个后台实例。

## 新 Linux 主机部署（脚本模式）

RHEL 8.10 x86_64 的整包交付优先使用 [`deploy/portable/README.txt`](deploy/portable/README.txt)：包内已有 Linux Python 3.11 和 Python 依赖，解压到 `/data/vmcore-ui` 后运行一次 `install.sh`。便携部署的 Nginx 示例域名为 `vmcore.example.com`，对外监听 8080；首次部署前应替换为自己的域名。管理台只监听 `127.0.0.1:18080`。已安装旧包时使用包内 `upgrade.sh`，先备份并停服务，再覆盖程序，同时保留服务器现有 Nginx 配置。以下为分步安装方式。

RHEL 8.10、内部 YUM 源与 Nginx 的逐步命令见 [`deploy/RHEL8.10.md`](deploy/RHEL8.10.md)。

下面约定程序在 `/opt/vmcore-ui`，持久数据在 `/var/lib/vmcore-ui`。从当前 Windows 工作区打包时排除本机预览数据、虚拟环境和 Python 缓存；`ui` 目前可能尚未提交到 Git，不要只在 Linux 上 `git clone` 后假定它已经存在。

```powershell
cd D:\Repo\ai-app-hub
tar -czf vmcore-ui-deploy.tgz --exclude=ui/data-preview --exclude=ui/data --exclude=ui/.venv --exclude='*/__pycache__' --exclude='*.pyc' ui
scp .\vmcore-ui-deploy.tgz USER@HOST:/tmp/
```

在 Linux 主机上需要 **Python 3.11+、venv/pip、Nginx、bash**；`openssl` 用于生成会话密钥，`tar`、`curl` 用于安装和验收。管理台的 Python 库由 `requirements.txt` 安装到独立虚拟环境。脚本及 Agent 自身的依赖仍按原脚本部署，不包含在本包中。

Debian 12 / Ubuntu 24.04 示例：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx bash openssl tar curl
python3 --version
```

RHEL 9.2+ / 相应的 Rocky、AlmaLinux 9 示例（系统默认 `python3` 可能低于 3.11）：

```bash
sudo dnf install -y python3.11 python3.11-pip nginx bash openssl tar curl
python3.11 --version
```

先确认 `/data/analytics_vmcore.sh`、`/data/vmcore`、`/data/vmcore-analysis-agent/reports` 已按脚本的原部署方式就绪。下面 `PYTHON_BIN` 在 Debian 12 / Ubuntu 24.04 取 `python3`，在 RHEL 9.2+ 取 `python3.11`，并核实版本为 3.11 或更高。然后执行：

```bash
sudo useradd --system --home-dir /var/lib/vmcore-ui --shell /usr/sbin/nologin vmcoreui
PYTHON_BIN=python3
sudo install -d -o root -g root -m 755 /opt/vmcore-ui
sudo tar -xzf /tmp/vmcore-ui-deploy.tgz -C /opt/vmcore-ui --strip-components=1
sudo "$PYTHON_BIN" -m venv /opt/vmcore-ui/.venv
sudo /opt/vmcore-ui/.venv/bin/python -m pip install -r /opt/vmcore-ui/requirements.txt
sudo install -d -o vmcoreui -g vmcoreui -m 700 /var/lib/vmcore-ui
sudo install -o vmcoreui -g vmcoreui -m 600 /opt/vmcore-ui/deploy/config.production.json /var/lib/vmcore-ui/config.json
sudo install -o root -g root -m 600 /opt/vmcore-ui/deploy/vmcore-ui.env.example /etc/vmcore-ui.env
sudoedit /etc/vmcore-ui.env
sudo install -o root -g root -m 644 /opt/vmcore-ui/deploy/vmcore-ui.service /etc/systemd/system/vmcore-ui.service
sudo systemctl daemon-reload
sudo systemctl enable --now vmcore-ui
```

编辑 `/etc/vmcore-ui.env` 时设置自己的内部密码和至少 24 字符的随机 `UI_SESSION_SECRET`，可用 `openssl rand -base64 48` 生成。文件由 root 持有，权限为 `600`。生产配置中的目录必须是 Linux 主机上的真实绝对路径；如果有调整，用 `sudoedit /var/lib/vmcore-ui/config.json` 修改，再执行 `sudo chown vmcoreui:vmcoreui /var/lib/vmcore-ui/config.json && sudo chmod 600 /var/lib/vmcore-ui/config.json`。脚本模式可将 `symbol_roots` 设为空数组，版本和符号由脚本处理。确保 `vmcoreui` 对 vmcore、脚本有读取及目录穿越权限，对报告输出目录有脚本所需的写入权限；实际权限要按脚本依赖的其他文件和凭据一并配置。建议以该用户手动运行一次已知样本来验证脚本和报告落盘位置，再从 UI 发起任务。

```bash
sudo -u vmcoreui test -r /data/analytics_vmcore.sh
sudo -u vmcoreui test -r /data/vmcore/某次崩溃/vmcore
sudo -u vmcoreui test -w /data/vmcore-analysis-agent/reports
systemctl status vmcore-ui --no-pager
journalctl -u vmcore-ui -n 100 --no-pager
curl -I http://127.0.0.1:8080/
```

服务仅监听 `127.0.0.1:8080`。单人验收可在自己的电脑运行 `ssh -L 8080:127.0.0.1:8080 USER@HOST`，然后打开 `http://127.0.0.1:8080/`。供多人访问时使用包内的 `deploy/vmcore-ui.conf`：先准备内部域名与可信 TLS 证书，再复制和编辑其中的 `server_name`、`ssl_certificate`、`ssl_certificate_key`，然后检查并加载 Nginx 配置。

```bash
sudo install -m 644 /opt/vmcore-ui/deploy/vmcore-ui.conf /etc/nginx/conf.d/vmcore-ui.conf
sudoedit /etc/nginx/conf.d/vmcore-ui.conf
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

Nginx 配置将 HTTP 80 重定向到 HTTPS 443，并代理至本机 8080。Markdown 导入最大 10 MB，配置已将 `client_max_body_size` 设为 12 MB。把 `/etc/vmcore-ui.env` 中的 `UI_SECURE_COOKIE` 设为 `1`，执行 `sudo systemctl restart vmcore-ui`，仅向需要访问的内网开放 80/443，不要直接对外开放 8080。部署后用真实目录创建一个任务，确认脚本收到的是 vmcore 父目录，且任务详情中的原始 Markdown 路径和归档报告对应同一 vmcore。升级前先停止服务并备份整个 `/var/lib/vmcore-ui`。

示例配置见 [`config.example.json`](config.example.json)。脚本模式默认从 `/data/vmcore-analysis-agent/reports` 查找本次报告，设置中的“脚本报告输出目录”可调整。`stable_seconds` 默认为 30 秒：扫描结果会根据修改时间标注“就绪”，任务真正开始前还会等待至少该时长、比较文件大小与修改时间，并再次检查目录白名单。脚本模式要求同目录的 vmcore 和 vmcore-dmesg 都稳定，版本和符号由脚本处理；HTTP 模式仍需 dmesg、vmlinux 和相关符号匹配。目录扫描不跟随符号链接到白名单之外。

脚本运行前记录报告目录的 Markdown 清单，脚本退出码为 0 后再次比对，仅考虑本次新增或修改的 `.md`。若报告明确写有 vmcore 路径，必须与任务 vmcore 一致；多份候选报告会报错供人工检查。`<报告名>.audit.md` 自动配对。选中的原始路径写入任务记录，正文和审计报告分别复制到 `data/reports/<任务ID>.md`、`data/audits/<任务ID>.md`，脚本的完整标准输出及标准错误写入 `data/logs/<任务ID>.log`，失败任务也保留日志。脚本模式串行执行，避免共享报告目录中并发产物串案。任务扫描优先读取 crash `sys` 的 NODENAME、DATE、RELEASE；没有匹配 vmlinux 时尝试 `crash --osrelease vmcore` 读取内核版本，再使用 dmesg 中的明确字段。OS 从 crash 输出、dmesg 或报告中的明确字段提取，不根据内核版本猜测。扫描将主机名、OS、内核、vmlinux 路径和故障时间写入 vmcore 父目录的 `osinfo.txt`；再次扫描会先验证路径、文件大小/修改时间。管理台服务用户需有 vmcore 父目录写入权限，失败原因可在任务详情查看。任务页把大小及首、中、尾各 1 MiB 抽样指纹相同的 vmcore 标为“疑似重复”，此标记不等同于完整文件哈希校验。

任务页首次使用时点击“重新扫描”建立 `data/discovery_cache.json`。之后打开任务页只遍历目录并读取文件大小与修改时间；已经扫描且未变化的 vmcore 直接复用缓存，新增或变化的文件才重新读取 crash 元数据、符号并计算抽样指纹。点击“重新扫描”会重新扫描全部文件；扫描配置变化后也需要重新建立缓存。

## 部署、恢复与备份

- 使用单实例、单 Uvicorn worker，始终串行执行任务。队列排序、任务及状态持续写入 JSON；重启时运行中和排队中的任务标记为中断，暂停且尚未执行的任务保留。
- “停止任务”将排队任务标为已中断；运行中的脚本会收到终止信号并停止进程组。HTTP 模式会停止管理台的流式请求，Agent 侧没有取消 API，需按 Agent 自身能力确认远端执行状态。
- 每次启动会把上次遗留的“排队中”“运行中”任务改为“已中断”，说明原因，保留既有记录，由操作员点击“重试”。后台没有 Agent 侧任务恢复接口，不自动续跑。
- 每次 JSON / Markdown 写入都先写同目录临时文件、`fsync`，再原子替换。备份时建议先停止管理台，复制**整个 `data` 目录**（任务、崩溃记录、复核、配置、审计日志、概览手动统计等 JSON 文件以及 `reports/`、`audits/`、`logs/`），再启动。这样任务元数据与报告文件处于同一备份时间点。恢复时停止管理台、整体替换 `data` 目录，再启动；保留备份的文件权限。只有单个 JSON 文件的备份不够。
- 允许目录建议只读挂载；`data` 目录仅管理台进程可写。日志和报告可能含主机路径与故障信息，按内部运维资料保管。

## 数据与统计口径

`scan_roots` 建议设置为 `/data/vmcore`。该目录下的**一级子目录**是“目录批次”，可以包含多层主机或时间文件夹。概览展示每个批次发现的 vmcore 数、文件稳定数、崩溃数、运行数、结果分歧数和待复核数。每个 vmcore 绝对路径代表一次独立崩溃；同一主机的不同 vmcore 分为不同的日期编码（`VM-YYYYMMDDHHMMSS-路径摘要`）。同一 vmcore 的分析、重试及历史导入报告归入同一崩溃编码。

概览中的“批量分析未处理”支持全部批次或单个批次：只为文件稳定、且尚无成功/排队/运行任务的 vmcore 创建任务。脚本模式由脚本识别版本；HTTP 模式另要求 dmesg 与 vmlinux 已匹配。未就绪文件和已有任务分别计数并返回；队列最多暂存 100 项，满时返回已创建数量，后续可再次执行。任务列表可重新分析或停止任务。

报告查询按主机、崩溃编码、报告三层聚合，每层按最近报告时间倒序。脚本目录和 UI 归档中的 Markdown 都参与索引；没有任务记录的外部报告也可参与对比与复核。报告对比与人工复核在一个页面进行，逐项展示所有报告的 AI 结论，人工可填写自己的判断、最终崩溃原因及评分。复核保存所选报告的 AI 快照；新增报告会使该崩溃重新进入待复核。删除报告会删除对应 Markdown，保留运行历史并清除该次崩溃的旧复核，便于重新确认。报告字段只提取明确出现的 `NODENAME`、`DATE`、`RELEASE`、`PANIC` 和报告内标注的 AI 结论；无法可靠提取的字段显示“未知”。

- 任务成功率：实际执行且状态为成功或失败的任务中，成功任务的比例；历史导入和中断/排队/运行任务不入分母。
- 平均 AI 耗时：成功且记录了耗时的实际执行任务的算术平均。
- 平均人工耗时：人工录入了耗时的复核记录的算术平均。
- 平均节省时间：有人工耗时且能配对复核时所选成功任务耗时的崩溃记录，逐项算 `人工耗时 - AI 耗时` 后平均；负值表示 AI 更慢，并显示配对样本数。
- 逐项准确率：仅纳入已核对全部现有报告的崩溃记录。AI 字段已知且人工判为“正确 / 部分正确 / 错误”的项目中，“正确”的比例；“部分正确”单列计数，不计入正确分子；“待确认”不入分母。新增报告后旧复核暂不进入该准确率，直到再次核对。每项显示样本数。
- 常见故障类型按每次崩溃计数，不按报告份数重复计算；已完成复核的记录优先使用人工故障类型，未知类型不计入排行。

## 验证

从仓库根目录运行：

```bash
python -m pytest ui/tests -q
```
