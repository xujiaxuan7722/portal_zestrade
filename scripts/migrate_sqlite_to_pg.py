#!/usr/bin/env python3
"""
migrate_sqlite_to_pg.py — 把旧的 SQLite（portal.db）数据迁到 PostgreSQL

用法（在项目根目录）：
    source .venv/bin/activate
    DATABASE_URL=postgresql://portal:portal@127.0.0.1:5432/portal \
        python scripts/migrate_sqlite_to_pg.py [--force]

行为：
  - 自动建表（如不存在）
  - PG 的 modules 表已有数据时拒绝迁移（避免重复导入）；--force 会先清空再导
  - 保留原 id / 排序 / 收藏关系；导完把自增序列拨到 MAX(id)
  - 找不到 portal.db 时只建表（等应用启动时播种）
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import json

from app import db  # noqa: E402

SQLITE_PATH = Path(__file__).resolve().parent.parent / "portal.db"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="清空 PG 现有数据后重新导入")
    args = parser.parse_args()

    db.create_schema()
    print(f"[1/4] PG 表结构就绪（{db.DATABASE_URL.split('@')[-1]}）")

    if not SQLITE_PATH.exists():
        print(f"[2/4] 未找到 {SQLITE_PATH.name}，无旧数据可迁；应用首次启动会自动播种")
        return

    with db.pool().connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM modules").fetchone()["c"]
        if count and not args.force:
            sys.exit(f"PG 的 modules 表已有 {count} 条数据，拒绝重复导入（加 --force 清空重导）")
        if count:
            conn.execute("TRUNCATE user_favorites, modules RESTART IDENTITY CASCADE")
            conn.execute("TRUNCATE known_roles")
            print("[2/4] --force：已清空 PG 现有数据")
        else:
            print("[2/4] PG 为空库，开始导入")

    lite = sqlite3.connect(SQLITE_PATH)
    lite.row_factory = sqlite3.Row

    modules = lite.execute("SELECT * FROM modules ORDER BY id").fetchall()
    roles = lite.execute("SELECT * FROM known_roles").fetchall()
    try:
        favs = lite.execute("SELECT * FROM user_favorites").fetchall()
    except sqlite3.OperationalError:
        favs = []  # 老库还没有这张表

    with db.pool().connection() as conn:
        for m in modules:
            conn.execute(
                "INSERT INTO modules (id, name, description, icon, url, category,"
                " sort_order, visible_roles, enabled)"
                " OVERRIDING SYSTEM VALUE VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    m["id"], m["name"], m["description"], m["icon"], m["url"],
                    m["category"], m["sort_order"],
                    json.loads(m["visible_roles"]), bool(m["enabled"]),
                ),
            )
        for r in roles:
            conn.execute(
                "INSERT INTO known_roles (name, source, last_seen) VALUES (%s, %s, %s)"
                " ON CONFLICT (name) DO NOTHING",
                (r["name"], r["source"],
                 datetime.fromtimestamp(r["last_seen"], tz=timezone.utc)),
            )
        for f in favs:
            conn.execute(
                "INSERT INTO user_favorites (user_id, module_id, position)"
                " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (f["user_id"], f["module_id"], f["position"]),
            )
        conn.execute(
            "SELECT setval(pg_get_serial_sequence('modules', 'id'),"
            " GREATEST((SELECT COALESCE(MAX(id), 1) FROM modules), 1))"
        )
    lite.close()
    print(f"[3/4] 导入完成：模块 {len(modules)} 条、角色 {len(roles)} 条、收藏 {len(favs)} 条")
    print(f"[4/4] 建议确认无误后归档旧库：mv {SQLITE_PATH.name} {SQLITE_PATH.name}.bak")


if __name__ == "__main__":
    try:
        main()
    finally:
        db.pool().close()
