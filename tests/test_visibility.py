"""可见性过滤（后端是安全边界）：/api/modules 按权限码、/api/announcements 按角色、
admin 403。不连数据库：db 层函数 monkeypatch；require_auth 用 dependency_overrides 注入。"""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import AuthInfo, require_auth
from app.main import app, module_visible

MODULES = [
    {"id": 1, "name": "公共", "requires": [], "enabled": True},
    {"id": 2, "name": "PM", "requires": ["pm_system:read:sku", "pm_system:read:spu"], "enabled": True},
    {"id": 3, "name": "MRP", "requires": ["mrp_system"], "enabled": True},   # 裸系统前缀
    {"id": 4, "name": "RBAC", "requires": ["admin:*"], "enabled": True},
    {"id": 5, "name": "停用", "requires": [], "enabled": False},
]

ANNS = [
    {"id": 1, "content": "全员通知", "visible_roles": [], "enabled": True},
    {"id": 2, "content": "仅财务", "visible_roles": ["finance"], "enabled": True},
]


def _auth(roles=(), perms=()):
    return AuthInfo(
        matched=True,
        matched_by="test",
        user={
            "id": f"u-{'-'.join(roles) or 'none'}",
            "display_name": "测试用户",
            "email": "t@zestrade.com",
            "mobile": "",
            "status": "active",
        },
        roles=[{"id": r, "name": r, "source": "test"} for r in roles],
        permissions=list(perms),
        contacts={},
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.db, "list_modules", lambda: [dict(m) for m in MODULES])
    monkeypatch.setattr(
        main.db, "list_active_announcements", lambda: [dict(a) for a in ANNS]
    )
    # TestClient 不用 with：不触发 lifespan，也就不会去连数据库
    yield TestClient(app)
    app.dependency_overrides.clear()


def _as(client, roles=(), perms=()):
    app.dependency_overrides[require_auth] = lambda: _auth(roles, perms)
    return client


def _module_names(client, roles=(), perms=()):
    return [m["name"] for m in _as(client, roles, perms).get("/api/modules").json()["modules"]]


# ── /api/modules：按权限码 ──


def test_no_permission_sees_only_public(client):
    assert _module_names(client) == ["公共"]  # RBAC 大量角色权限为 0：必须仍能看到公开应用


def test_exact_code_match(client):
    assert _module_names(client, perms=["pm_system:read:sku"]) == ["公共", "PM"]


def test_bare_system_prefix_in_requires(client):
    assert _module_names(client, perms=["mrp_system:write:bom"]) == ["公共", "MRP"]


def test_user_system_wildcard_matches_codes(client):
    assert _module_names(client, perms=["pm_system:*"]) == ["公共", "PM"]


def test_legacy_wildcard_code(client):
    assert _module_names(client, perms=["admin:*"]) == ["公共", "RBAC"]


def test_star_sees_all_enabled(client):
    assert _module_names(client, perms=["*"]) == ["公共", "PM", "MRP", "RBAC"]  # 停用仍隐藏


def test_unrelated_permission_hidden(client):
    assert _module_names(client, perms=["crm:read:orders"]) == ["公共"]


def test_module_visible_unit():
    assert module_visible([], [])
    assert module_visible(["a:b"], ["*"])
    assert module_visible(["pm_system"], ["pm_system:read:sku"])
    assert module_visible(["pm_system:*"], ["pm_system:read:sku"])  # requires 配系统通配
    assert not module_visible(["pm_system:read:sku"], ["mrp_system:read:bom"])
    assert not module_visible(["pm_system:read:sku"], [])


# ── /api/announcements：仍按角色 ──


def test_announcements_filtered_by_role(client):
    hr = [a["content"] for a in _as(client, roles=["hr"]).get("/api/announcements").json()["announcements"]]
    assert hr == ["全员通知"]
    fin = [a["content"] for a in _as(client, roles=["finance"]).get("/api/announcements").json()["announcements"]]
    assert fin == ["全员通知", "仅财务"]


# ── 管理接口门禁：仍按 admin 角色 ──


def test_non_admin_gets_403_on_admin_api(client):
    assert _as(client, roles=["finance"]).get("/api/admin/modules").status_code == 403


def test_admin_can_access_admin_api(client):
    assert _as(client, roles=["admin"]).get("/api/admin/modules").status_code == 200
