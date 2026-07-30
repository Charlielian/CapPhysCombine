#!/usr/bin/env bash
# Start CapPhysCombine service
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/.server.pid"
LOG_FILE="$SCRIPT_DIR/logs/server.log"

mkdir -p "$SCRIPT_DIR/logs"

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Server is already running (PID: $OLD_PID)"
        echo "Use ./restart.sh to restart, or ./stop.sh to stop first."
        exit 1
    fi
    rm -f "$PID_FILE"
fi

echo "Starting CapPhysCombine..."
cd "$SCRIPT_DIR"
nohup python CapPhysCombine.py serve >> "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"
echo "Server started (PID: $PID)"
echo "Log: $LOG_FILE"
