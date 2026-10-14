#!/bin/sh
# Upgrade an existing /data/vmcore-ui installation from a portable bundle.
set -eu

BASE=/data/vmcore-ui
ARCHIVE=${1:-}
if [ "$(id -u)" -ne 0 ]; then
    echo "请用 root 执行升级脚本" >&2
    exit 1
fi
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
    echo "用法: sh upgrade.sh /path/vmcore-ui-rhel8.10-x86_64.tar.gz" >&2
    exit 1
fi
if [ ! -f "$BASE/data/config.json" ] || [ ! -f "$BASE/.env" ]; then
    echo "未找到既有安装的数据和登录配置，请使用 install.sh 首次安装" >&2
    exit 1
fi

ARCHIVE=$(readlink -f "$ARCHIVE")
tar -tzf "$ARCHIVE" >/dev/null
if ! tar -tzf "$ARCHIVE" | grep -Fxq install.sh; then
    echo "整包缺少 install.sh" >&2
    exit 1
fi

BACKUP_DIR=/data/vmcore-ui-backups
install -d -m 700 "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/vmcore-ui-$(date +%Y%m%d-%H%M%S)-$$.tar.gz"
NGINX_BACKUP="$BACKUP_DIR/vmcore-ui-nginx-$(date +%Y%m%d-%H%M%S)-$$.conf"
NGINX_EXISTED=0
if [ -f /etc/nginx/conf.d/vmcore-ui.conf ]; then
    cp -p /etc/nginx/conf.d/vmcore-ui.conf "$NGINX_BACKUP"
    chmod 600 "$NGINX_BACKUP"
    NGINX_EXISTED=1
fi
systemctl stop vmcore-ui
if ! tar -czf "$BACKUP" -C "$BASE" .; then
    systemctl start vmcore-ui || true
    echo "备份失败，旧服务已尝试恢复" >&2
    exit 1
fi
chmod 600 "$BACKUP"

rollback() {
    result=$?
    if [ "$result" -ne 0 ]; then
        echo "升级失败，正在从 $BACKUP 恢复旧版本" >&2
        systemctl stop vmcore-ui || true
        tar -xzf "$BACKUP" -C "$BASE" || true
        install -o root -g root -m 644 "$BASE/vmcore-ui.service" /etc/systemd/system/vmcore-ui.service || true
        if [ "$NGINX_EXISTED" -eq 1 ]; then
            cp -p "$NGINX_BACKUP" /etc/nginx/conf.d/vmcore-ui.conf || true
        else
            rm -f /etc/nginx/conf.d/vmcore-ui.conf
        fi
        systemctl daemon-reload || true
        systemctl start vmcore-ui || true
        nginx -t && systemctl reload nginx || true
    fi
}
trap rollback EXIT

tar --no-same-owner -xzf "$ARCHIVE" -C "$BASE"
sh "$BASE/install.sh"
systemctl is-active --quiet vmcore-ui
trap - EXIT
echo "升级完成，备份文件：$BACKUP"
