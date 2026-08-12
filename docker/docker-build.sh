#!/usr/bin/env bash
# 构建镜像（照 pm_system 口径：优先读 shell 的 APP_IMAGE，其次读根目录 .env）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT_DIR}"

if [ -z "${APP_IMAGE:-}" ] && [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

IMAGE_REF="${APP_IMAGE:-portal-zestrade:latest}"

BUILD_ARGS=()
for proxy_var in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy PIP_INDEX_URL; do
  if [ -n "${!proxy_var:-}" ]; then
    BUILD_ARGS+=(--build-arg "${proxy_var}=${!proxy_var}")
  fi
done

docker build "${BUILD_ARGS[@]}" -t "${IMAGE_REF}" -f Dockerfile .

echo "built ${IMAGE_REF}"
