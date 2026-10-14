RHEL 8.10 x86_64 离线整包

前提：目标主机已有 Nginx，以及原分析脚本 /data/analytics_vmcore.sh 和其 Agent 运行环境。
本包包括管理台、Python 3.11、FastAPI 等 Python 依赖，不使用目标机的内部 PyPI。

将本包上传到目标主机后执行：
  sudo mkdir -p /data/vmcore-ui
  sudo tar -xzf vmcore-ui-rhel8.10-x86_64.tar.gz -C /data/vmcore-ui
  sudo sh /data/vmcore-ui/install.sh

已经安装旧包时，请在整包所在目录执行升级脚本，不要在服务运行中手动解压：
  tar -xOzf vmcore-ui-rhel8.10-x86_64.tar.gz upgrade.sh > /tmp/vmcore-ui-upgrade.sh
  sudo sh /tmp/vmcore-ui-upgrade.sh "$(realpath vmcore-ui-rhel8.10-x86_64.tar.gz)"
升级脚本停服务后备份整个 /data/vmcore-ui，再覆盖解压、运行安装脚本并检查服务。
备份保存在 /data/vmcore-ui-backups；升级失败时会恢复旧目录并重启旧服务。
已有 data/config.json、历史报告、.env 登录密码和现有 Nginx 配置均保留。
升级会按现有 data/config.json 检查脚本报告目录，必要时用 setfacl 赋予 vmcoreui 读写权限；
若目标机缺少 setfacl 或目录仍不可访问，升级失败并恢复旧 UI。安装前可用
sudo -u vmcoreui test -w /data/vmcore-analysis-agent/reports 检查。

安装脚本会创建管理台数据与随机登录密码，注册并启动 systemd 服务，复制
vmcore-ui.conf 到 /etc/nginx/conf.d/，检查并重载 Nginx。示例域名为 vmcore.example.com；
部署前请替换为自己的域名，并让该域名解析到目标主机 IP。换域名时修改
/etc/nginx/conf.d/vmcore-ui.conf 的 server_name 并重载 Nginx。
后续重新运行 install.sh 会保留已存在的 Nginx 配置。
当前为 HTTP 8080，内网测试后建议接入 HTTPS。
读取初始密码：sudo cat /data/vmcore-ui/data/initial-password.txt

管理台路径：/data/vmcore-ui
扫描路径：/data/vmcore
脚本路径：/data/analytics_vmcore.sh
脚本参数：vmcore 父目录相对 /data/vmcore 的路径，例如 127.0.0.1/file
脚本报告路径示例：/data/vmcore-analysis-agent/reports；部署时填写真实路径
任务页：首次点击“重新扫描”建立扫描缓存；再次进入时只检查新增或已变化的文件，
已有且未变化的 vmcore 跳过 crash 查询、符号匹配和内容抽样。点击“重新扫描”才完整扫描。
扫描时保留现有表格，扫描完成且结果变化才更新；疑似重复 vmcore 单列卡片；
所有数据表格的表头均有清晰分隔线，可拖动调整列宽并保存在当前浏览器；
也可聚焦分隔线后按左右方向键调整。每次脚本运行的完整输出保存在 data/logs 并显示于任务详情。
任务扫描：递归查找同目录中的 vmcore 和 vmcore-dmesg，按父目录创建时间倒序。
主机、故障时间、内核：匹配 vmlinux 且系统已有 crash 命令时优先读取 crash sys；
缺少匹配符号时使用 dmesg 中的明确字段，无法核实的显示“未知”。
OS 名称：从报告内明确字段读取；不由内核版本推测发行版。
扫描会在 vmcore 父目录写入 osinfo.txt，服务用户 vmcoreui 需要该目录的写入权限。
已有 osinfo.txt 必须通过 vmcore、dmesg、vmlinux 文件签名及字段校验才会复用。

安装脚本不会重置密码或历史。
备份请复制整个 /data/vmcore-ui/data 目录。
概览页的手动统计保存在 data/dashboard_overrides.json，随整个 data 目录一同备份。
