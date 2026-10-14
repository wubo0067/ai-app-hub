#!/bin/sh
set -eu

BASE=/data/vmcore-ui
if [ "$(id -u)" -ne 0 ]; then
    echo "请用 root 执行: sudo sh /data/vmcore-ui/install.sh" >&2
    exit 1
fi
if [ "$(uname -m)" != x86_64 ]; then
    echo "此包适用于 RHEL 8.10 x86_64" >&2
    exit 1
fi
if [ ! -x "$BASE/python/bin/python3.11" ]; then
    echo "内置 Python 缺失或没有执行权限；请用 tar -xzf 解压" >&2
    exit 1
fi
if ! command -v nginx >/dev/null 2>&1; then
    echo "目标机需要已有 Nginx" >&2
    exit 1
fi

if ! id vmcoreui >/dev/null 2>&1; then
    useradd --system --home-dir "$BASE/data" --shell /sbin/nologin vmcoreui
fi
install -d -o vmcoreui -g vmcoreui -m 700 "$BASE/data"
if [ ! -f "$BASE/data/config.json" ]; then
    install -o vmcoreui -g vmcoreui -m 600 "$BASE/config.production.json" "$BASE/data/config.json"
fi
"$BASE/python/bin/python3.11" "$BASE/ensure_report_access.py" "$BASE/data/config.json"
if [ ! -f "$BASE/.env" ]; then
    PASSWORD=$($BASE/python/bin/python3.11 -c 'import secrets; print(secrets.token_urlsafe(24))')
    SECRET=$($BASE/python/bin/python3.11 -c 'import secrets; print(secrets.token_urlsafe(48))')
    umask 077
    printf 'UI_ADMIN_PASSWORD=%s\nUI_SESSION_SECRET=%s\nUI_SECURE_COOKIE=0\n' "$PASSWORD" "$SECRET" > "$BASE/.env"
    printf '%s\n' "$PASSWORD" > "$BASE/data/initial-password.txt"
    chown root:root "$BASE/data/initial-password.txt"
    chmod 600 "$BASE/.env" "$BASE/data/initial-password.txt"
fi

"$BASE/python/bin/python3.11" -V
PYTHONPATH="$BASE/site-packages:$BASE" "$BASE/python/bin/python3.11" -c 'import fastapi, uvicorn, httpx, pydantic_core; print("Python 依赖检查通过")'
install -o root -g root -m 644 "$BASE/vmcore-ui.service" /etc/systemd/system/vmcore-ui.service
systemctl daemon-reload
systemctl enable vmcore-ui
systemctl restart vmcore-ui

if [ ! -f /etc/nginx/conf.d/vmcore-ui.conf ]; then
    install -o root -g root -m 644 "$BASE/vmcore-ui.conf" /etc/nginx/conf.d/vmcore-ui.conf
fi
if command -v restorecon >/dev/null 2>&1; then
    restorecon -v /etc/nginx/conf.d/vmcore-ui.conf || true
fi
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = Enforcing ]; then
    setsebool -P httpd_can_network_connect 1
fi
nginx -t
systemctl enable --now nginx
systemctl reload nginx
if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-port=8080/tcp
    firewall-cmd --reload
fi
echo "管理台已安装；请按 Nginx server_name 配置访问域名和 8080 端口"
echo "初始登录密码: sudo cat $BASE/data/initial-password.txt"
echo "分析脚本须已存在: /data/analytics_vmcore.sh"
