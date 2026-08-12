#!/usr/bin/env sh
set -eu

cd /app

HOST="${BACKEND_HOST:-0.0.0.0}"
PORT="${BACKEND_PORT:-8200}"
WORKERS="${UVICORN_WORKERS:-1}"

# 注意：多 worker 时权限缓存各自独立（README「已知边界」），角色变更最多延迟 5 分钟
if [ "${WORKERS}" -gt 1 ] 2>/dev/null; then
  exec python -m uvicorn app.main:app --host "${HOST}" --port "${PORT}" --workers "${WORKERS}"
fi

exec python -m uvicorn app.main:app --host "${HOST}" --port "${PORT}"
