#!/usr/bin/env bash
# 重启服务：先执行带优雅等待的 stop，再启动新进程；任一步失败都会由 set -e 终止。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

"$SCRIPT_DIR/stop.sh"
"$SCRIPT_DIR/start.sh"
