"""
db.py — PostgreSQL 数据层（psycopg3 + 连接池）

连接：环境变量 DATABASE_URL。仅本地开发（AUTH_BYPASS=true）允许缺省回落本地默认库；
生产模式缺省即拒绝启动（防止静默连上弱口令开发库）。

表结构版本化：migrations/*.sql 按文件名顺序执行，schema_migrations 表记录已执行版本
（应用启动时自动跑）。改表 = 新增编号递增的 SQL 文件，勿改已执行过的文件。

四张表（完整 DDL 见 migrations/*.sql，设计说明见 README「数据结构」）：
  modules         门户展示的应用模块（可见权限码 requires TEXT[]、负责人、维护状态）
  user_favorites  用户常用应用（个人工作台"我的常用应用"，外键级联删除）
  announcements   横幅公告（门户顶部条；可见性同应用，按权限码 requires 过滤）
  audit_logs      管理操作审计（谁在何时对什么做了什么）
角色制残留（visible_roles 列、known_roles 表）已在 003 迁移中删除，
可见性统一为权限码，RBAC 为唯一事实源。
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

# 仅本地开发（AUTH_BYPASS=true）允许缺省回落本地默认库；生产模式漏配 DATABASE_URL
# 会静默连上弱口令开发库——与 auth 的 bypass 防呆同款思路：直接拒绝启动
_AUTH_BYPASS = os.getenv("AUTH_BYPASS", "false").strip().lower() in ("1", "true", "yes")
DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    if not _AUTH_BYPASS:
        raise RuntimeError(
            "未配置 DATABASE_URL——生产模式（未开 AUTH_BYPASS）不回落本地默认库，"
            "请在 .env 中配置生产库连接串"
        )
    DATABASE_URL = "postgresql://portal:portal@127.0.0.1:5432/portal"

_pool: Optional[ConnectionPool] = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


_SEED_MODULES = [
    # (name, description, icon, url, category, requires)
    # icon 为 frontend/icons.js 内置图标名
    # 除领星/钉钉/RBAC 外，URL 为占位内网域名，真实地址确认后在管理后台修改即可
    ("ECSP 电商销售规划", "销售计划、市场与链接规划", "cart", "https://ecsp.zestrade.com", "电商运营", []),
    ("领星 ERP", "亚马逊店铺运营与广告数据", "rocket", "https://erp.lingxing.com", "电商运营", []),
    ("ERP 库存/订单主干", "库存、订单与主数据中枢", "package", "https://erp.zestrade.com", "供应链生产", []),
    ("Pf 采购请购跟进", "采购请购与到货跟进", "clipboard", "https://pf.zestrade.com", "供应链生产", []),
    ("PRS 派工报工", "工厂派工与报工管理", "factory", "https://prs.zestrade.com", "供应链生产", []),
    ("PM 产品系统", "Petsfit 产品资料与生命周期", "paw", "https://pm.zestrade.com", "产品设计", []),
    ("设计中心", "设计需求与素材管理", "palette", "https://design.zestrade.com", "产品设计", []),
    ("CRM 客户销售", "客户与销售机会管理", "users", "https://crm.zestrade.com", "客户销售", []),
    ("钉钉 OA", "审批、考勤与内部沟通", "chat", "https://oa.dingtalk.com", "协同办公", []),
    ("RBAC 权限管理", "统一角色与权限配置后台", "shield", "https://rbac.bogoo.ai", "", ["admin:*"]),
]


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def run_migrations() -> None:
    """按文件名顺序执行 migrations/*.sql，schema_migrations 里已记录的跳过。
    每个迁移与其版本记录在同一事务提交（psycopg 连接上下文退出即 commit），
    失败则整体回滚、异常上抛——应用拒绝启动，不会带着半套表结构跑。"""
    with pool().connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  version    TEXT PRIMARY KEY,"
            "  applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {
            r["version"]
            for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.stem in applied:
            continue
        with pool().connection() as conn:
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,)
            )


def seed_if_empty() -> None:
    with pool().connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM modules").fetchone()["c"]
        if count:
            return
        for i, (name, desc, icon, url, cat, requires) in enumerate(_SEED_MODULES):
            conn.execute(
                "INSERT INTO modules (name, description, icon, url, category, sort_order, requires)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (name, desc, icon, url, cat, i, requires),
            )


def init_db() -> None:
    run_migrations()
    seed_if_empty()


def close_pool() -> None:
    """应用关闭时调用：优雅关闭连接池（否则退出时池子工作线程被硬掐断）。"""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


# ============================================================
# modules
# ============================================================


def _row_to_module(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "icon": row["icon"],
        "url": row["url"],
        "category": row["category"],
        "sort_order": row["sort_order"],
        "requires": list(row["requires"] or []),
        "enabled": row["enabled"],
        "status": row["status"],
        "owner_name": row["owner_name"],
    }


def list_modules() -> list[dict]:
    with pool().connection() as conn:
        rows = conn.execute("SELECT * FROM modules ORDER BY sort_order, id").fetchall()
    return [_row_to_module(r) for r in rows]


def get_module(module_id: int) -> dict | None:
    with pool().connection() as conn:
        row = conn.execute(
            "SELECT * FROM modules WHERE id = %s", (module_id,)
        ).fetchone()
    return _row_to_module(row) if row else None


def create_module(data: dict) -> dict:
    with pool().connection() as conn:
        row = conn.execute(
            "INSERT INTO modules (name, description, icon, url, category, sort_order,"
            " requires, enabled, status, owner_name)"
            " VALUES (%s, %s, %s, %s, %s,"
            "   (SELECT COALESCE(MAX(sort_order), -1) + 1 FROM modules),"
            "   %s, %s, %s, %s)"
            " RETURNING *",
            (
                data["name"], data["description"], data["icon"], data["url"],
                data["category"], data["requires"], data["enabled"],
                data["status"], data["owner_name"],
            ),
        ).fetchone()
    return _row_to_module(row)


def update_module(module_id: int, data: dict) -> dict | None:
    with pool().connection() as conn:
        row = conn.execute(
            "UPDATE modules SET name=%s, description=%s, icon=%s, url=%s, category=%s,"
            " requires=%s, enabled=%s, status=%s, owner_name=%s, updated_at=now()"
            " WHERE id=%s RETURNING *",
            (
                data["name"], data["description"], data["icon"], data["url"],
                data["category"], data["requires"], data["enabled"],
                data["status"], data["owner_name"],
                module_id,
            ),
        ).fetchone()
    return _row_to_module(row) if row else None


def delete_module(module_id: int) -> bool:
    with pool().connection() as conn:
        cur = conn.execute("DELETE FROM modules WHERE id = %s", (module_id,))
    return cur.rowcount > 0


def reorder_modules(ids: list[int]) -> None:
    with pool().connection() as conn:
        for order, module_id in enumerate(ids):
            conn.execute(
                "UPDATE modules SET sort_order = %s, updated_at = now() WHERE id = %s",
                (order, module_id),
            )


# ============================================================
# user_favorites
# ============================================================


def get_favorites(user_id: str) -> list[int]:
    """用户的常用应用模块 id，按用户自定顺序。"""
    with pool().connection() as conn:
        rows = conn.execute(
            "SELECT module_id FROM user_favorites WHERE user_id = %s"
            " ORDER BY position, module_id",
            (user_id,),
        ).fetchall()
    return [r["module_id"] for r in rows]


def set_favorites(user_id: str, ids: list[int]) -> None:
    """整表替换该用户的常用应用（顺序即列表顺序）。"""
    with pool().connection() as conn:
        conn.execute("DELETE FROM user_favorites WHERE user_id = %s", (user_id,))
        for pos, module_id in enumerate(ids):
            conn.execute(
                "INSERT INTO user_favorites (user_id, module_id, position)"
                " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (user_id, module_id, pos),
            )


# ============================================================
# announcements
# ============================================================


def _row_to_announcement(row: dict) -> dict:
    return {
        "id": row["id"],
        "content": row["content"],
        "level": row["level"],
        "starts_at": row["starts_at"].isoformat() if row["starts_at"] else None,
        "ends_at": row["ends_at"].isoformat() if row["ends_at"] else None,
        "requires": list(row["requires"] or []),
        "enabled": row["enabled"],
    }


def list_announcements() -> list[dict]:
    """全部公告（管理后台用）。"""
    with pool().connection() as conn:
        rows = conn.execute(
            "SELECT * FROM announcements ORDER BY id DESC"
        ).fetchall()
    return [_row_to_announcement(r) for r in rows]


def list_active_announcements() -> list[dict]:
    """当前生效的公告（门户用；角色过滤在路由层做）。"""
    with pool().connection() as conn:
        rows = conn.execute(
            "SELECT * FROM announcements WHERE enabled"
            " AND (starts_at IS NULL OR starts_at <= now())"
            " AND (ends_at IS NULL OR ends_at >= now())"
            " ORDER BY id DESC"
        ).fetchall()
    return [_row_to_announcement(r) for r in rows]


def create_announcement(data: dict) -> dict:
    with pool().connection() as conn:
        row = conn.execute(
            "INSERT INTO announcements (content, level, starts_at, ends_at, requires, enabled)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
            (
                data["content"], data["level"], data["starts_at"], data["ends_at"],
                data["requires"], data["enabled"],
            ),
        ).fetchone()
    return _row_to_announcement(row)


def update_announcement(ann_id: int, data: dict) -> dict | None:
    with pool().connection() as conn:
        row = conn.execute(
            "UPDATE announcements SET content=%s, level=%s, starts_at=%s, ends_at=%s,"
            " requires=%s, enabled=%s, updated_at=now() WHERE id=%s RETURNING *",
            (
                data["content"], data["level"], data["starts_at"], data["ends_at"],
                data["requires"], data["enabled"], ann_id,
            ),
        ).fetchone()
    return _row_to_announcement(row) if row else None


def delete_announcement(ann_id: int) -> bool:
    with pool().connection() as conn:
        cur = conn.execute("DELETE FROM announcements WHERE id = %s", (ann_id,))
    return cur.rowcount > 0


# ============================================================
# audit_logs
# ============================================================


def log_action(
    actor: str, action: str, target_type: str,
    target_id: Any = "", detail: Optional[dict] = None,
) -> None:
    with pool().connection() as conn:
        conn.execute(
            "INSERT INTO audit_logs (actor, action, target_type, target_id, detail)"
            " VALUES (%s, %s, %s, %s, %s)",
            (actor, action, target_type, str(target_id), Jsonb(detail or {})),
        )


def list_audit_logs(limit: int = 100) -> list[dict]:
    with pool().connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_logs ORDER BY id DESC LIMIT %s", (limit,)
        ).fetchall()
    return [
        {
            "id": r["id"],
            "actor": r["actor"],
            "action": r["action"],
            "target_type": r["target_type"],
            "target_id": r["target_id"],
            "detail": r["detail"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
