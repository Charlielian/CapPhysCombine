#!/usr/bin/env bash
# Stop CapPhysCombine service
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/.server.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found. Server may not be running."
    exit 0
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    echo "Stopping server (PID: $PID)..."
    kill "$PID"
    # Wait up to 5 seconds for graceful shutdown
    for i in $(seq 1 10); do
        if ! kill -0 "$PID" 2>/dev/null; then
            break
        fi
        sleep 0.5
    done
    # Force kill if still alive
    if kill -0 "$PID" 2>/dev/null; then
        echo "Graceful shutdown timed out, sending SIGKILL..."
        kill -9 "$PID" 2>/dev/null || true
    fi
    echo "Server stopped."
else
    echo "Process $PID is not running (stale PID file)."
fi

rm -f "$PID_FILE"
