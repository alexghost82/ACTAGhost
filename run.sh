#!/usr/bin/env bash
# Start ACTA GHOST (with the AGENT VLM subsystem) as a detached background
# server that survives the launching shell. Logs to .acta/server.log.
#
# Usage:
#   ./run.sh            # start (vision enabled), detached
#   ./run.sh stop       # stop the running server
#   ./run.sh status     # show whether it is listening
#   ./run.sh foreground # run in the foreground (Ctrl+C to quit)
set -euo pipefail

cd "$(dirname "$0")"

HOST="${ACTA_HOST:-127.0.0.1}"
PORT="${ACTA_PORT:-8765}"
PY="${ACTA_PYTHON:-.venv/bin/python}"
LOG_DIR=".acta"
LOG_FILE="${LOG_DIR}/server.log"
PID_FILE="${LOG_DIR}/server.pid"

export ACTA_VISION_ENABLED="${ACTA_VISION_ENABLED:-true}"
export ACTA_VLM_PROVIDER="${ACTA_VLM_PROVIDER:-auto}"

mkdir -p "$LOG_DIR"

cmd="${1:-start}"

case "$cmd" in
  foreground)
    exec "$PY" -m uvicorn acta.api.app:app --host "$HOST" --port "$PORT" --log-level info
    ;;
  stop)
    if [[ -f "$PID_FILE" ]]; then
      kill "$(cat "$PID_FILE")" 2>/dev/null || true
      rm -f "$PID_FILE"
      echo "stopped"
    else
      pkill -f "uvicorn acta.api.app:app" 2>/dev/null || true
      echo "stopped (by pattern)"
    fi
    ;;
  status)
    if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "running on http://$HOST:$PORT"
    else
      echo "not running"
    fi
    ;;
  start)
    if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "already running on http://$HOST:$PORT"
      exit 0
    fi
    # Fully detach via a Python double-fork + setsid so the server survives the
    # launching shell (portable; no external `setsid` needed).
    "$PY" - "$HOST" "$PORT" "$LOG_FILE" "$PID_FILE" <<'PYEOF'
import os, sys
host, port, log_file, pid_file = sys.argv[1:5]
# First fork
if os.fork() > 0:
    os._exit(0)
os.setsid()
# Second fork — fully detach from the controlling terminal/session.
if os.fork() > 0:
    os._exit(0)
with open(pid_file, "w") as fh:
    fh.write(str(os.getpid()))
fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
os.dup2(fd, 1)
os.dup2(fd, 2)
devnull = os.open(os.devnull, os.O_RDONLY)
os.dup2(devnull, 0)
os.execvp(sys.executable, [
    sys.executable, "-u", "-m", "uvicorn", "acta.api.app:app",
    "--host", host, "--port", port, "--log-level", "info",
])
PYEOF
    sleep 4
    echo "starting ACTA GHOST on http://$HOST:$PORT (logs: $LOG_FILE)"
    ;;
  *)
    echo "usage: $0 [start|stop|status|foreground]" >&2
    exit 2
    ;;
esac
