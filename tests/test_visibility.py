"""可见性过滤（后端是安全边界）：应用与公告统一按权限码，管理接口门禁按
PORTAL_MANAGE_PERMISSION 权限码判定。不连数据库：db 层函数 monkeypatch；
require_auth 用 dependency_overrides 注入指定权限。"""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import AuthInfo, require_auth
from app.main import PORTAL_MANAGE_PERMISSION, app, requires_visible

MODULES = [
    {"id": 1, "name": "公共", "requires": [], "enabled": True},
    {"id": 2, "name": "PM", "requires": ["pm_system:read:sku", "pm_system:read:spu"], "enabled": True},
    {"id": 3, "name": "MRP", "requires": ["mrp_system"], "enabled": True},   # 裸系统前缀
    {"id": 4, "name": "RBAC", "requires": ["admin:*"], "enabled": True},
    {"id": 5, "name": "停用", "requires": [], "enabled": False},
]

ANNS = [
    {"id": 1, "content": "全员通知", "requires": [], "enabled": True},
    {"id": 2, "content": "仅PM", "requires": ["pm_system"], "enabled": True},
]


def _auth(perms=()):
    return AuthInfo(
        matched=True,
        matched_by="test",
        user={
            "id": "u-test",
            "display_name": "测试用户",
            "email": "t@zestrade.com",
            "mobile": "",
            "status": "active",
        },
        roles=[],
        permissions=list(perms),
        contacts={},
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.db, "list_modules", lambda: [dict(m) for m in MODULES])
    monkeypatch.setattr(
        main.db, "list_active_announcements", lambda: [dict(a) for a in ANNS]
    )
    monkeypatch.setattr(main.db, "get_category_order", lambda: [])
    monkeypatch.setattr(main.db, "get_category_colors", lambda: {})
    # TestClient 不用 with：不触发 lifespan，也就不会去连数据库
    yield TestClient(app)
    app.dependency_overrides.clear()


def _as(client, perms=()):
    app.dependency_overrides[require_auth] = lambda: _auth(perms)
    return client


def _module_names(client, perms=()):
    return [m["name"] for m in _as(client, perms).get("/api/modules").json()["modules"]]


# ── /api/modules ──


def test_no_permission_sees_only_public(client):
    assert _module_names(client) == ["公共"]  # RBAC 大量角色权限为 0：必须仍能看到公开应用


def test_exact_code_match(client):
    assert _module_names(client, ["pm_system:read:sku"]) == ["公共", "PM"]


def test_bare_system_prefix_in_requires(client):
    assert _module_names(client, ["mrp_system:write:bom"]) == ["公共", "MRP"]


def test_user_system_wildcard_matches_codes(client):
    assert _module_names(client, ["pm_system:*"]) == ["公共", "PM"]


def test_legacy_wildcard_code(client):
    assert _module_names(client, ["admin:*"]) == ["公共", "RBAC"]


def test_star_sees_all_enabled(client):
    assert _module_names(client, ["*"]) == ["公共", "PM", "MRP", "RBAC"]  # 停用仍隐藏


def test_unrelated_permission_hidden(client):
    assert _module_names(client, ["crm:read:orders"]) == ["公共"]


def test_requires_visible_unit():
    assert requires_visible([], [])
    assert requires_visible(["a:b"], ["*"])
    assert requires_visible(["pm_system"], ["pm_system:read:sku"])
    assert requires_visible(["pm_system:*"], ["pm_system:read:sku"])  # requires 配系统通配
    assert not requires_visible(["pm_system:read:sku"], ["mrp_system:read:bom"])
    assert not requires_visible(["pm_system:read:sku"], [])


# ── /api/announcements：与应用同一套规则 ──


def test_announcements_filtered_by_permission(client):
    none = [a["content"] for a in _as(client).get("/api/announcements").json()["announcements"]]
    assert none == ["全员通知"]
    pm = [a["content"] for a in _as(client, ["pm_system:read:sku"]).get("/api/announcements").json()["announcements"]]
    assert pm == ["全员通知", "仅PM"]


# ── 管理接口门禁：按 PORTAL_MANAGE_PERMISSION 权限码 ──


def test_no_permission_gets_403_on_admin_api(client):
    assert _as(client, ["pm_system:*"]).get("/api/admin/modules").status_code == 403


def test_star_holder_can_access_admin_api(client):
    assert _as(client, ["*"]).get("/api/admin/modules").status_code == 200  # 现 admin 持 *


def test_exact_portal_code_can_access_admin_api(client):
    assert _as(client, [PORTAL_MANAGE_PERMISSION]).get("/api/admin/modules").status_code == 200


def test_portal_wildcard_can_access_admin_api(client):
    assert _as(client, ["portal:*"]).get("/api/admin/modules").status_code == 200
