#!/usr/bin/env bash
# 本地开发启动脚本：默认开启 AUTH_BYPASS（绕过认证，返回全权限测试用户）
# 上线部署时不要用本脚本，参考 README 的上线清单。
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

export AUTH_BYPASS=${AUTH_BYPASS:-true}
exec uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
