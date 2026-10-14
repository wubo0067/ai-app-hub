# RHEL 8.10 部署（内部 YUM 源，Nginx）

管理台独立运行于 `/opt/vmcore-ui`，数据存放于 `/var/lib/vmcore-ui`。Nginx 监听 80/443 并代理本机的 `127.0.0.1:8080`。分析采用现有脚本 `/data/analytics_vmcore.sh`，脚本及其依赖的 Agent 需另行按原有方式部署。

## 拷贝清单

| 来源 | 目标 | 用途 |
| --- | --- | --- |
| 仓库根目录 `vmcore-ui-deploy.tgz` | `/tmp/vmcore-ui-deploy.tgz` | 管理台代码、静态页面、requirements、配置模板与 systemd/Nginx 文件 |
| 现有 `analytics_vmcore.sh` | `/data/analytics_vmcore.sh` | 分析入口，接收单个 vmcore 的父目录 |
| 原 Agent 项目、依赖、模型凭据 | 保持脚本实际引用的路径 | 使脚本可独立运行；不包含在管理台包内 |
| 原始 `vmcore` 与配套 `dmesg` 等 | `/data/vmcore/<案例目录>/` | 待分析文件；若目标主机已有则无需搬运 |
| 内部 TLS 证书和私钥 | `/etc/pki/tls/certs/vmcore-ui.crt`、`/etc/pki/tls/private/vmcore-ui.key` | Nginx HTTPS；按实际域名签发 |
| 旧管理台数据（仅迁移已有正式记录时） | `/var/lib/vmcore-ui/` | 须拷贝整个数据目录，包括 JSON、报告和审计 Markdown |

不要拷贝 Windows 虚拟环境、`ui/data-preview` 或缓存文件。内部 YUM 源提供操作系统 RPM；还需有可访问的内部 PyPI 镜像，或者准备 Python wheel 包（见下文）。

在 Windows 工作区打包并传送：

```powershell
cd D:\Repo\ai-app-hub
tar -czf vmcore-ui-deploy.tgz --exclude=ui/data-preview --exclude=ui/data --exclude=ui/.venv --exclude='*/__pycache__' --exclude='*.pyc' ui
scp .\vmcore-ui-deploy.tgz USER@HOST:/tmp/
```

## 安装 RPM 和 Python 依赖

确认内部源包含 RHEL 8 BaseOS、AppStream。`python3.11` 和 `python3.11-pip` 由 AppStream 提供；使用 3.11 专属命令，不替换系统 Python。

```bash
cat /etc/redhat-release
uname -m
sudo dnf repolist
sudo dnf install -y python3.11 python3.11-pip nginx bash openssl tar curl policycoreutils
python3.11 --version
```

如果内部源有 PyPI 镜像，先在主机或 pip 配置中设置对应地址，再按后面的安装命令执行。若只能访问 YUM 源，在**同为 RHEL 8.10、同 CPU 架构、Python 3.11**且可访问 PyPI 的机器上准备 wheel 包：

> 当前管理台要求 `fastapi>=0.110`。若内部 PyPI 最高只有 FastAPI 0.83，直接执行 requirements 安装会因版本约束失败；需将当前依赖及其传递依赖制作成 wheel 包带入。仅安装 0.83 会影响启动生命周期和请求模型处理。

```bash
python3.11 -m venv /tmp/vmcore-wheel-env
/tmp/vmcore-wheel-env/bin/python -m pip download -r ui/requirements.txt -d wheelhouse
tar -czf vmcore-wheelhouse.tgz wheelhouse
```

将 `vmcore-wheelhouse.tgz` 另行拷贝到目标机 `/tmp/`；安装时使用 `--no-index --find-links=/tmp/wheelhouse`。wheel 必须匹配目标架构及 Python 版本，不能从 Windows 虚拟环境直接复制。`requirements.txt` 也包含用于验收的 pytest。

## 安装管理台

确认脚本、vmcore、报告目录和原 Agent 已在目标机就位。执行：

```bash
sudo useradd --system --home-dir /var/lib/vmcore-ui --shell /sbin/nologin vmcoreui
sudo install -d -o root -g root -m 755 /opt/vmcore-ui
sudo tar -xzf /tmp/vmcore-ui-deploy.tgz -C /opt/vmcore-ui --strip-components=1
sudo python3.11 -m venv /opt/vmcore-ui/.venv
sudo /opt/vmcore-ui/.venv/bin/python -m pip install -r /opt/vmcore-ui/requirements.txt
sudo install -d -o vmcoreui -g vmcoreui -m 700 /var/lib/vmcore-ui
sudo install -o vmcoreui -g vmcoreui -m 600 /opt/vmcore-ui/deploy/config.production.json /var/lib/vmcore-ui/config.json
sudo install -o root -g root -m 600 /opt/vmcore-ui/deploy/vmcore-ui.env.example /etc/vmcore-ui.env
sudo install -o root -g root -m 644 /opt/vmcore-ui/deploy/vmcore-ui.service /etc/systemd/system/vmcore-ui.service
```

只有 wheel 包时，将 pip 安装一行换成：

```bash
sudo tar -xzf /tmp/vmcore-wheelhouse.tgz -C /tmp
sudo /opt/vmcore-ui/.venv/bin/python -m pip install --no-index --find-links=/tmp/wheelhouse -r /opt/vmcore-ui/requirements.txt
```

编辑 `/etc/vmcore-ui.env`：填入 `UI_ADMIN_PASSWORD`，用 `openssl rand -base64 48` 生成至少 24 字符的 `UI_SESSION_SECRET`，HTTPS 模式设 `UI_SECURE_COOKIE=1`。核对 `/var/lib/vmcore-ui/config.json` 的 `scan_roots`、`script_path`、`report_roots` 与真实路径一致。服务用户须能读取 vmcore 和脚本，并能写入脚本报告目录；脚本调用 Agent 所需权限也按原环境配置。

```bash
sudoedit /etc/vmcore-ui.env
sudoedit /var/lib/vmcore-ui/config.json
sudo chown vmcoreui:vmcoreui /var/lib/vmcore-ui/config.json
sudo chmod 600 /var/lib/vmcore-ui/config.json
sudo -u vmcoreui test -r /data/analytics_vmcore.sh
sudo -u vmcoreui test -w /data/vmcore-analysis-agent/reports
sudo systemctl daemon-reload
sudo systemctl enable --now vmcore-ui
curl -I http://127.0.0.1:8080/
```

可选的脚本验收（选择一个已完成写入的真实目录）：`sudo -u vmcoreui bash /data/analytics_vmcore.sh /data/vmcore/<案例目录>`。该命令会执行真实分析并生成报告。

## 配置 Nginx、SELinux 和防火墙

先将与域名匹配的证书和私钥传至目标机的当前目录，分别命名为 `vmcore-ui.crt` 和 `vmcore-ui.key`，再核对 `server_name`。默认配置的 80 端口跳转至 443，后端仍只监听本机 8080。Nginx 代理本地后端时，在 SELinux enforcing 模式下启用反向代理网络连接布尔值。

```bash
sudo install -o root -g root -m 644 vmcore-ui.crt /etc/pki/tls/certs/vmcore-ui.crt
sudo install -o root -g root -m 600 vmcore-ui.key /etc/pki/tls/private/vmcore-ui.key
sudo install -o root -g root -m 644 /opt/vmcore-ui/deploy/vmcore-ui.conf /etc/nginx/conf.d/vmcore-ui.conf
sudoedit /etc/nginx/conf.d/vmcore-ui.conf
sudo restorecon -v /etc/pki/tls/certs/vmcore-ui.crt /etc/pki/tls/private/vmcore-ui.key /etc/nginx/conf.d/vmcore-ui.conf
sudo setsebool -P httpd_can_network_connect 1
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

如果 firewalld 正在运行，仅开放 Nginx 的 80/443，保持 8080 不对外开放：

```bash
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

检查：`systemctl status vmcore-ui nginx --no-pager`、`journalctl -u vmcore-ui -n 100 --no-pager`、`curl -kI https://<内部域名>/`。优先用可信内部 CA 证书，并以浏览器正常访问验证证书；`-k` 只用于首轮连通性检查。首次用真实案例目录创建任务，确认报告位于任务详情，且原始 Markdown 路径来自配置的 reports 目录。

升级前停止管理台并备份整个 `/var/lib/vmcore-ui`，包括 `config.json`、任务与案例 JSON、分析和审计 Markdown。单实例运行；不启动多个 Uvicorn worker。
