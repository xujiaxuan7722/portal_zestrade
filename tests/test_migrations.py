"""迁移物料与启动防呆的静态检查（不连数据库）：文件命名可排序且版本唯一、
基线含全部五张表、Docker 构建不落下 migrations 目录、生产漏配 DATABASE_URL 时 fail-fast。"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))


def test_migration_files_numbered_and_unique():
    assert MIGRATIONS, "migrations 目录不能为空"
    prefixes = [p.stem.split("_")[0] for p in MIGRATIONS]
    assert all(re.fullmatch(r"\d{3}", x) for x in prefixes), "文件名须以三位数字编号开头"
    assert len(set(prefixes)) == len(prefixes), "版本编号重复会破坏执行顺序"


def test_baseline_contains_all_five_tables():
    sql = MIGRATIONS[0].read_text(encoding="utf-8")
    for table in ("modules", "known_roles", "user_favorites", "announcements", "audit_logs"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql


def test_dockerfile_copies_migrations():
    assert "COPY migrations" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


def _import_db(env_overrides):
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DATABASE_URL", "AUTH_BYPASS")
    }
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import app.db"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )


def test_db_failfast_without_database_url_in_prod_mode():
    proc = _import_db({})
    assert proc.returncode != 0
    assert "DATABASE_URL" in proc.stderr


def test_db_falls_back_to_dev_default_under_bypass():
    proc = _import_db({"AUTH_BYPASS": "true"})
    assert proc.returncode == 0, proc.stderr
