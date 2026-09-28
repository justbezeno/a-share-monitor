#!/bin/bash
# install_autoupdate.sh — 一键安装「每日自动更新」（macOS launchd）
#
# ⚠️ 必须在**你自己的系统终端**里运行，不要在受限沙盒 / 受管终端内执行：
#    这类环境中的进程通常无权操作 launchd（launchctl bootstrap 会报 Input/output error，
#    crontab 直接 operation not permitted），属环境限制，不是配置错误。
#
# 用法：
#     cd <本目录>
#     bash install_autoupdate.sh
#
# 卸载：
#     bash install_autoupdate.sh --uninstall

set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.arisk.update"
TEMPLATE="$DIR/com.arisk.update.plist.example"
PLIST="$DIR/$LABEL.plist"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"
DOMAIN="gui/$UID_NUM"

if [ "${1:-}" = "--uninstall" ]; then
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null && echo "已卸载 launchd 任务"
    rm -f "$AGENT" && echo "已删除 $AGENT"
    exit 0
fi

# 1. 生成 plist（把模板里的 __ARISK_DIR__ 换成实际绝对路径）
if [ ! -f "$TEMPLATE" ]; then
    echo "✗ 找不到模板 $TEMPLATE" >&2; exit 1
fi
sed "s|__ARISK_DIR__|$DIR|g" "$TEMPLATE" > "$PLIST"
if ! plutil -lint "$PLIST" >/dev/null; then
    echo "✗ 生成的 plist 未通过校验" >&2; exit 1
fi
echo "✓ 已生成 $PLIST"

# 2. 装入 ~/Library/LaunchAgents（并确保日志目录存在）
mkdir -p "$HOME/Library/LaunchAgents" "$DIR/logs"
cp "$PLIST" "$AGENT"
echo "✓ 已安装 $AGENT"

# 3. 幂等加载：先卸载旧的，再加载新的
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
if ! launchctl bootstrap "$DOMAIN" "$AGENT"; then
    echo "✗ launchctl bootstrap 失败。请在系统终端重试，或查看：" >&2
    echo "    launchctl print $DOMAIN/$LABEL" >&2
    exit 1
fi
launchctl enable "$DOMAIN/$LABEL" 2>/dev/null

# 4. 验证
if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    echo "✓ 已加载：$LABEL"
    echo
    launchctl print "$DOMAIN/$LABEL" | /usr/bin/grep -E "^\s*(state|path|program) " | head -4
else
    echo "✗ 未能确认加载状态，请手动检查：launchctl print $DOMAIN/$LABEL" >&2
    exit 1
fi

cat <<EOF

计划：每天 16:10 / 17:10 / … / 22:10 各判断一次，数据落后才抓（已最新则秒退）。

  立即试跑一次 : launchctl kickstart -p $DOMAIN/$LABEL
  查看更新日志 : tail -f "$DIR/arisk_update.log"
  查看 launchd : tail -f "$DIR/logs/launchd_update.log" "$DIR/logs/launchd_update.err"
  卸载         : bash "$DIR/install_autoupdate.sh" --uninstall
EOF
