#!/usr/bin/env bash
# 服务器更新流程：拉取当前分支最新代码 → 重新构建镜像 → 重启容器
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE="${GIT_REMOTE:-origin}"
BRANCH="${GIT_BRANCH:-$(git -C "${ROOT_DIR}" rev-parse --abbrev-ref HEAD)}"

cd "${ROOT_DIR}"

git fetch "${REMOTE}" "${BRANCH}"
git checkout "${BRANCH}"
git pull --ff-only "${REMOTE}" "${BRANCH}"

"${ROOT_DIR}/docker/docker-build.sh"
"${ROOT_DIR}/docker/docker-deploy.sh"
