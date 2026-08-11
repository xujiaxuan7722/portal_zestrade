"""
db.py — SQLite 数据层

两张表：
  modules      门户展示的应用模块（名称/图标/URL/排序/可见角色/启用状态）
  known_roles  已知角色名池：管理后台"可见角色"下拉框的数据源。
               RBAC 目前没有角色列表接口，所以采用"自动收录 + 手动添加"：
               每次用户访问 /api/me，把 RBAC 返回的角色名收录进来（source=seen）；
               管理员也可在后台手动添加（source=manual）。
               以后 RBAC 提供了角色接口，只需把 list_known_roles 换成远程调用。
"""

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "portal.db"

_SEED_MODULES = [
    # (name, description, icon, url, category, visible_roles)
    # 除领星/钉钉/RBAC 外，URL 为占位内网域名，真实地址确认后在管理后台修改即可
    # 分类为空 = 门户侧边栏归入"未分类"
    ("ECSP 电商销售规划", "销售计划、市场与链接规划", "🛒", "https://ecsp.zestrade.com", "电商运营", []),
    ("领星 ERP", "亚马逊店铺运营与广告数据", "🚀", "https://erp.lingxing.com", "电商运营", []),
    ("ERP 库存/订单主干", "库存、订单与主数据中枢", "📦", "https://erp.zestrade.com", "供应链生产", []),
    ("Pf 采购请购跟进", "采购请购与到货跟进", "📋", "https://pf.zestrade.com", "供应链生产", []),
    ("PRS 派工报工", "工厂派工与报工管理", "🏭", "https://prs.zestrade.com", "供应链生产", []),
    ("PM 产品系统", "Petsfit 产品资料与生命周期", "🐾", "https://pm.zestrade.com", "产品设计", []),
    ("设计中心", "设计需求与素材管理", "🎨", "https://design.zestrade.com", "产品设计", []),
    ("CRM 客户销售", "客户与销售机会管理", "🤝", "https://crm.zestrade.com", "客户销售", []),
    ("钉钉 OA", "审批、考勤与内部沟通", "💬", "https://oa.dingtalk.com", "协同办公", []),
    ("RBAC 权限管理", "统一角色与权限配置后台", "🛡️", "https://rbac.bogoo.ai", "", ["admin"]),
]


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS modules (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          name          TEXT    NOT NULL,
          description   TEXT    NOT NULL DEFAULT '',
          icon          TEXT    NOT NULL DEFAULT '📦',
          url           TEXT    NOT NULL,
          category      TEXT    NOT NULL DEFAULT '',
          sort_order    INTEGER NOT NULL DEFAULT 0,
          visible_roles TEXT    NOT NULL DEFAULT '[]',
          enabled       INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS known_roles (
          name      TEXT PRIMARY KEY,
          source    TEXT NOT NULL DEFAULT 'seen',
          last_seen REAL NOT NULL
        );
        """
    )
    # 老库迁移：补 category 列，并按种子模块名回填分类
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(modules)")}
    if "category" not in cols:
        conn.execute("ALTER TABLE modules ADD COLUMN category TEXT NOT NULL DEFAULT ''")
        for name, *_, cat, _roles in _SEED_MODULES:
            if cat:
                conn.execute(
                    "UPDATE modules SET category = ? WHERE name = ? AND category = ''",
                    (cat, name),
                )
    if conn.execute("SELECT COUNT(*) c FROM modules").fetchone()["c"] == 0:
        for i, (name, desc, icon, url, cat, roles) in enumerate(_SEED_MODULES):
            conn.execute(
                "INSERT INTO modules (name, description, icon, url, category, sort_order, visible_roles)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, desc, icon, url, cat, i, json.dumps(roles, ensure_ascii=False)),
            )
        seed_roles = sorted({r for *_, roles in _SEED_MODULES for r in roles})
        for r in seed_roles:
            conn.execute(
                "INSERT OR IGNORE INTO known_roles (name, source, last_seen) VALUES (?, 'manual', ?)",
                (r, time.time()),
            )
    conn.commit()
    conn.close()


def _row_to_module(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "icon": row["icon"],
        "url": row["url"],
        "category": row["category"],
        "sort_order": row["sort_order"],
        "visible_roles": json.loads(row["visible_roles"]),
        "enabled": bool(row["enabled"]),
    }


def list_modules() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM modules ORDER BY sort_order, id").fetchall()
    conn.close()
    return [_row_to_module(r) for r in rows]


def get_module(module_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM modules WHERE id = ?", (module_id,)).fetchone()
    conn.close()
    return _row_to_module(row) if row else None


def create_module(data: dict) -> dict:
    conn = get_conn()
    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) m FROM modules"
    ).fetchone()["m"]
    cur = conn.execute(
        "INSERT INTO modules (name, description, icon, url, category, sort_order, visible_roles, enabled)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            data["name"], data["description"], data["icon"], data["url"],
            data["category"],
            max_order + 1,
            json.dumps(data["visible_roles"], ensure_ascii=False),
            int(data["enabled"]),
        ),
    )
    conn.commit()
    module_id = cur.lastrowid
    conn.close()
    return get_module(module_id)


def update_module(module_id: int, data: dict) -> dict | None:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE modules SET name=?, description=?, icon=?, url=?, category=?, visible_roles=?, enabled=?"
        " WHERE id=?",
        (
            data["name"], data["description"], data["icon"], data["url"],
            data["category"],
            json.dumps(data["visible_roles"], ensure_ascii=False),
            int(data["enabled"]),
            module_id,
        ),
    )
    conn.commit()
    conn.close()
    return get_module(module_id) if cur.rowcount else None


def delete_module(module_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM modules WHERE id = ?", (module_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def reorder_modules(ids: list[int]) -> None:
    conn = get_conn()
    for order, module_id in enumerate(ids):
        conn.execute(
            "UPDATE modules SET sort_order = ? WHERE id = ?", (order, module_id)
        )
    conn.commit()
    conn.close()


def record_roles(names: list[str], source: str = "seen") -> None:
    """把出现过的角色名收录进池子（已存在则只刷新 last_seen，不覆盖 source）。"""
    if not names:
        return
    now = time.time()
    conn = get_conn()
    for name in names:
        name = (name or "").strip()
        if not name:
            continue
        conn.execute(
            "INSERT INTO known_roles (name, source, last_seen) VALUES (?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET last_seen = excluded.last_seen",
            (name, source, now),
        )
    conn.commit()
    conn.close()


def list_known_roles() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM known_roles ORDER BY name").fetchall()
    conn.close()
    return [
        {"name": r["name"], "source": r["source"], "last_seen": r["last_seen"]}
        for r in rows
    ]


def modules_using_role(name: str) -> list[dict]:
    """仍在 visible_roles 里引用该角色的模块（删角色前的联动检查）。"""
    return [m for m in list_modules() if name in m["visible_roles"]]


def remove_role_from_modules(name: str) -> int:
    """把角色名从所有模块的 visible_roles 中移除，返回受影响模块数。"""
    conn = get_conn()
    rows = conn.execute("SELECT id, visible_roles FROM modules").fetchall()
    changed = 0
    for r in rows:
        roles = json.loads(r["visible_roles"])
        if name in roles:
            conn.execute(
                "UPDATE modules SET visible_roles = ? WHERE id = ?",
                (json.dumps([x for x in roles if x != name], ensure_ascii=False), r["id"]),
            )
            changed += 1
    conn.commit()
    conn.close()
    return changed


def delete_known_role(name: str) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM known_roles WHERE name = ?", (name,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0
