"""
db.py — PostgreSQL 数据层（psycopg3 + 连接池）

连接：环境变量 DATABASE_URL（默认 postgresql://portal:portal@127.0.0.1:5432/portal）

五张表（完整 DDL 见 _SCHEMA，设计说明见 README「数据结构」）：
  modules         门户展示的应用模块（可见角色 TEXT[]、负责人、维护状态、时间戳）
  known_roles     已知角色名池：管理后台"可见角色"下拉框的数据源。
                  RBAC 目前没有角色列表接口，采用"自动收录 + 手动添加"；
                  以后 RBAC 提供了角色接口，把 list_known_roles 换成远程调用即可
  user_favorites  用户常用应用（个人工作台"我的常用应用"，外键级联删除）
  announcements   横幅公告（门户顶部条：维护通知/新系统上线等入口场景信息）
  audit_logs      管理操作审计（谁在何时对什么做了什么）
"""

import os
from datetime import datetime
from typing import Any, Optional

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://portal:portal@127.0.0.1:5432/portal"
)

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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS modules (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name          TEXT        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 50),
  description   TEXT        NOT NULL DEFAULT '' CHECK (char_length(description) <= 200),
  icon          TEXT        NOT NULL DEFAULT '📦' CHECK (char_length(icon) <= 200),
  url           TEXT        NOT NULL CHECK (char_length(url) BETWEEN 1 AND 500),
  category      TEXT        NOT NULL DEFAULT '' CHECK (char_length(category) <= 50),
  sort_order    INTEGER     NOT NULL DEFAULT 0,
  visible_roles TEXT[]      NOT NULL DEFAULT '{}',      -- 空数组 = 所有人可见
  enabled       BOOLEAN     NOT NULL DEFAULT TRUE,
  status        TEXT        NOT NULL DEFAULT 'normal'
                            CHECK (status IN ('normal', 'maintenance')),
  owner_name    TEXT        NOT NULL DEFAULT '' CHECK (char_length(owner_name) <= 50),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS known_roles (
  name       TEXT PRIMARY KEY CHECK (char_length(name) BETWEEN 1 AND 50),
  source     TEXT        NOT NULL DEFAULT 'seen' CHECK (source IN ('seen', 'manual')),
  last_seen  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_favorites (
  user_id    TEXT    NOT NULL CHECK (char_length(user_id) <= 200),
  module_id  BIGINT  NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  position   INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, module_id)
);
CREATE INDEX IF NOT EXISTS idx_user_favorites_user ON user_favorites (user_id);

CREATE TABLE IF NOT EXISTS announcements (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  content       TEXT        NOT NULL CHECK (char_length(content) BETWEEN 1 AND 200),
  level         TEXT        NOT NULL DEFAULT 'info' CHECK (level IN ('info', 'warning')),
  starts_at     TIMESTAMPTZ,                            -- NULL = 立即生效
  ends_at       TIMESTAMPTZ,                            -- NULL = 长期有效
  visible_roles TEXT[]      NOT NULL DEFAULT '{}',      -- 空数组 = 所有人可见
  enabled       BOOLEAN     NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor       TEXT        NOT NULL DEFAULT '',          -- 操作人（display_name/email）
  action      TEXT        NOT NULL,                     -- create/update/delete/reorder/...
  target_type TEXT        NOT NULL,                     -- module/role/announcement
  target_id   TEXT        NOT NULL DEFAULT '',
  detail      JSONB       NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs (created_at DESC);
"""

_SEED_MODULES = [
    # (name, description, icon, url, category, visible_roles)
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
    ("RBAC 权限管理", "统一角色与权限配置后台", "shield", "https://rbac.bogoo.ai", "", ["admin"]),
]


def create_schema() -> None:
    with pool().connection() as conn:
        conn.execute(_SCHEMA)


def seed_if_empty() -> None:
    with pool().connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM modules").fetchone()["c"]
        if count:
            return
        for i, (name, desc, icon, url, cat, roles) in enumerate(_SEED_MODULES):
            conn.execute(
                "INSERT INTO modules (name, description, icon, url, category, sort_order, visible_roles)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (name, desc, icon, url, cat, i, roles),
            )
        seed_roles = sorted({r for *_, roles in _SEED_MODULES for r in roles})
        for r in seed_roles:
            conn.execute(
                "INSERT INTO known_roles (name, source) VALUES (%s, 'manual')"
                " ON CONFLICT (name) DO NOTHING",
                (r,),
            )


def init_db() -> None:
    create_schema()
    seed_if_empty()


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
        "visible_roles": list(row["visible_roles"] or []),
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
            " visible_roles, enabled, status, owner_name)"
            " VALUES (%s, %s, %s, %s, %s,"
            "   (SELECT COALESCE(MAX(sort_order), -1) + 1 FROM modules),"
            "   %s, %s, %s, %s)"
            " RETURNING *",
            (
                data["name"], data["description"], data["icon"], data["url"],
                data["category"], data["visible_roles"], data["enabled"],
                data["status"], data["owner_name"],
            ),
        ).fetchone()
    return _row_to_module(row)


def update_module(module_id: int, data: dict) -> dict | None:
    with pool().connection() as conn:
        row = conn.execute(
            "UPDATE modules SET name=%s, description=%s, icon=%s, url=%s, category=%s,"
            " visible_roles=%s, enabled=%s, status=%s, owner_name=%s, updated_at=now()"
            " WHERE id=%s RETURNING *",
            (
                data["name"], data["description"], data["icon"], data["url"],
                data["category"], data["visible_roles"], data["enabled"],
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
# known_roles
# ============================================================


def record_roles(names: list[str], source: str = "seen") -> None:
    """把出现过的角色名收录进池子（已存在则只刷新 last_seen；seen 可覆盖 manual 来源，
    表示该角色已被真实用户带回、得到验证）。"""
    names = [n.strip() for n in names if n and n.strip()]
    if not names:
        return
    with pool().connection() as conn:
        for name in names:
            if source == "seen":
                conn.execute(
                    "INSERT INTO known_roles (name, source, last_seen) VALUES (%s, 'seen', now())"
                    " ON CONFLICT (name) DO UPDATE SET last_seen = now(), source = 'seen'",
                    (name,),
                )
            else:
                conn.execute(
                    "INSERT INTO known_roles (name, source, last_seen) VALUES (%s, %s, now())"
                    " ON CONFLICT (name) DO UPDATE SET last_seen = now()",
                    (name, source),
                )


def list_known_roles() -> list[dict]:
    with pool().connection() as conn:
        rows = conn.execute("SELECT * FROM known_roles ORDER BY name").fetchall()
    return [
        {"name": r["name"], "source": r["source"], "last_seen": r["last_seen"].isoformat()}
        for r in rows
    ]


def modules_using_role(name: str) -> list[dict]:
    """仍在 visible_roles 里引用该角色的模块（删角色前的联动检查）。"""
    with pool().connection() as conn:
        rows = conn.execute(
            "SELECT * FROM modules WHERE %s = ANY(visible_roles) ORDER BY sort_order, id",
            (name,),
        ).fetchall()
    return [_row_to_module(r) for r in rows]


def remove_role_from_modules(name: str) -> int:
    """把角色名从所有模块的 visible_roles 中移除，返回受影响模块数。"""
    with pool().connection() as conn:
        cur = conn.execute(
            "UPDATE modules SET visible_roles = array_remove(visible_roles, %s),"
            " updated_at = now() WHERE %s = ANY(visible_roles)",
            (name, name),
        )
    return cur.rowcount


def delete_known_role(name: str) -> bool:
    with pool().connection() as conn:
        cur = conn.execute("DELETE FROM known_roles WHERE name = %s", (name,))
    return cur.rowcount > 0


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
        "visible_roles": list(row["visible_roles"] or []),
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
            "INSERT INTO announcements (content, level, starts_at, ends_at, visible_roles, enabled)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
            (
                data["content"], data["level"], data["starts_at"], data["ends_at"],
                data["visible_roles"], data["enabled"],
            ),
        ).fetchone()
    return _row_to_announcement(row)


def update_announcement(ann_id: int, data: dict) -> dict | None:
    with pool().connection() as conn:
        row = conn.execute(
            "UPDATE announcements SET content=%s, level=%s, starts_at=%s, ends_at=%s,"
            " visible_roles=%s, enabled=%s, updated_at=now() WHERE id=%s RETURNING *",
            (
                data["content"], data["level"], data["starts_at"], data["ends_at"],
                data["visible_roles"], data["enabled"], ann_id,
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
