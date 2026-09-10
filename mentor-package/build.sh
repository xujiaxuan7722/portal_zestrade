#!/usr/bin/env bash
# 重新生成试跑包的两个大文件（镜像 tar + 演示数据 seed），在开发机上运行：
#   ./mentor-package/build.sh
# 前提：本机镜像 portal-zestrade:latest 已构建（./docker/docker-build.sh），
#       本机 PostgreSQL 的 portal 库为当前演示数据（DATABASE_URL 取自 .env，缺省本地默认库）。
set -euo pipefail
cd "$(dirname "$0")"
DB_URL="${DATABASE_URL:-$(grep -E '^DATABASE_URL=' ../.env 2>/dev/null | cut -d= -f2- || true)}"
DB_URL="${DB_URL:-postgresql://portal:portal@127.0.0.1:5432/portal}"

echo "1/2 导出镜像 portal-zestrade:mentor"
docker tag portal-zestrade:latest portal-zestrade:mentor
docker save portal-zestrade:mentor | gzip > portal-zestrade.tar.gz

echo "2/2 导出演示数据 seed/portal.sql（仅 public schema，不含审计日志）"
mkdir -p seed
pg_dump --no-owner --no-privileges --no-comments --schema=public \
  --exclude-table-data=public.audit_logs "$DB_URL" \
  | sed -e '/^\\restrict /d' -e '/^\\unrestrict /d' -e '/^CREATE SCHEMA public;$/d' \
  > seed/portal.sql
#   ↑ 去掉三行：\restrict/\unrestrict 是新版 pg_dump 的 psql 指令，旧版 psql 不认；
#     CREATE SCHEMA public 在 postgres 官方镜像初始化时会报"已存在"，而初始化脚本
#     是 ON_ERROR_STOP 模式，一报错整份 seed 就中止。

ls -lh portal-zestrade.tar.gz seed/portal.sql
echo "完成。把本目录整个发出去即可（含 docker-compose.yml / README.md）。"
