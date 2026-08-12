#!/usr/bin/env bash
# 按 docker-compose.deploy.yml 启动/更新容器（需先准备好 .env 与镜像）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.deploy.yml}"

cd "${ROOT_DIR}"

if [ ! -f .env ]; then
  echo "missing .env; copy .env.docker.example to .env and fill in real values" >&2
  exit 1
fi

docker compose -f "${COMPOSE_FILE}" up -d --remove-orphans
docker compose -f "${COMPOSE_FILE}" ps
