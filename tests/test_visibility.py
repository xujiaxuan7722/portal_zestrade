"""角色可见性过滤（后端是安全边界）：/api/modules、/api/announcements、admin 403。
不连数据库：db 层函数 monkeypatch；require_auth 用 dependency_overrides 注入指定角色。"""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import AuthInfo, require_auth
from app.main import app

MODULES = [
    {"id": 1, "name": "公共", "visible_roles": [], "enabled": True},
    {"id": 2, "name": "财务", "visible_roles": ["finance"], "enabled": True},
    {"id": 3, "name": "人事", "visible_roles": ["hr"], "enabled": True},
    {"id": 4, "name": "停用", "visible_roles": [], "enabled": False},
]

ANNS = [
    {"id": 1, "content": "全员通知", "visible_roles": [], "enabled": True},
    {"id": 2, "content": "仅财务", "visible_roles": ["finance"], "enabled": True},
]


def _auth(roles):
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
        permissions=[],
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


def _as(client, roles):
    app.dependency_overrides[require_auth] = lambda: _auth(roles)
    return client


def test_finance_sees_public_and_finance_only(client):
    names = [m["name"] for m in _as(client, ["finance"]).get("/api/modules").json()["modules"]]
    assert names == ["公共", "财务"]


def test_no_role_sees_only_public(client):
    names = [m["name"] for m in _as(client, []).get("/api/modules").json()["modules"]]
    assert names == ["公共"]


def test_admin_sees_all_enabled_modules(client):
    names = [m["name"] for m in _as(client, ["admin"]).get("/api/modules").json()["modules"]]
    assert names == ["公共", "财务", "人事"]  # 停用模块对 admin 也隐藏


def test_announcements_filtered_by_role(client):
    hr = [a["content"] for a in _as(client, ["hr"]).get("/api/announcements").json()["announcements"]]
    assert hr == ["全员通知"]
    fin = [a["content"] for a in _as(client, ["finance"]).get("/api/announcements").json()["announcements"]]
    assert fin == ["全员通知", "仅财务"]


def test_non_admin_gets_403_on_admin_api(client):
    assert _as(client, ["finance"]).get("/api/admin/modules").status_code == 403


def test_admin_can_access_admin_api(client):
    assert _as(client, ["admin"]).get("/api/admin/modules").status_code == 200
