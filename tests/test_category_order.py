"""门户分类块自定义顺序（/api/admin/category-order）与模块序列化的接入时间字段。
不连数据库：db 层函数 monkeypatch。"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.auth import AuthInfo, require_auth
from app.main import app


def _auth(perms=("*",)):
    return AuthInfo(
        matched=True, matched_by="test",
        user={"id": "u", "display_name": "测试管理员", "email": "a@zestrade.com",
              "mobile": "", "status": "active"},
        roles=[], permissions=list(perms), contacts={},
    )


class _FakeDB:
    def __init__(self):
        self.order = []
        self.logs = []

    def get_category_order(self):
        return list(self.order)

    def set_category_order(self, order):
        self.order = list(order)
        return list(order)

    def list_modules(self):
        return [{"id": 1, "name": "A", "requires": [], "enabled": True, "category": "电商运营"}]

    def log_action(self, *a, **k):
        self.logs.append((a, k))


@pytest.fixture
def fake(monkeypatch):
    f = _FakeDB()
    for name in ("get_category_order", "set_category_order", "list_modules", "log_action"):
        monkeypatch.setattr(main.db, name, getattr(f, name))
    return f


@pytest.fixture
def client(fake):
    app.dependency_overrides[require_auth] = lambda: _auth()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_default_is_empty(client):
    assert client.get("/api/admin/category-order").json() == {"order": []}


def test_put_cleans_and_audits(client, fake):
    r = client.put("/api/admin/category-order",
                   json={"order": [" 产品设计 ", "电商运营", "产品设计", "", "未分类", "客户销售"]})
    assert r.status_code == 200
    assert r.json()["order"] == ["产品设计", "电商运营", "客户销售"]
    assert fake.order == ["产品设计", "电商运营", "客户销售"]
    assert client.get("/api/admin/category-order").json()["order"] == fake.order
    (args, kw), = fake.logs
    assert args[1:3] == ("reorder", "category")
    assert kw["detail"] == {"order": ["产品设计", "电商运营", "客户销售"]}


def test_put_rejects_overlong_name(client):
    assert client.put("/api/admin/category-order", json={"order": ["x" * 51]}).status_code == 422


def test_requires_manage_permission(fake):
    app.dependency_overrides[require_auth] = lambda: _auth(perms=("pm_system:read:sku",))
    try:
        c = TestClient(app)
        assert c.get("/api/admin/category-order").status_code == 403
        assert c.put("/api/admin/category-order", json={"order": ["a"]}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_portal_modules_carry_category_order(client, fake):
    fake.order = ["客户销售", "电商运营"]
    body = client.get("/api/modules").json()
    assert body["category_order"] == ["客户销售", "电商运营"]
    assert [m["name"] for m in body["modules"]] == ["A"]


def test_module_serializer_exposes_timestamps():
    ts = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)
    row = {"id": 1, "name": "A", "description": "", "icon": "", "url": "https://a",
           "category": "", "sort_order": 0, "requires": None, "enabled": True,
           "status": "normal", "owner_name": "", "created_at": ts, "updated_at": ts}
    out = db._row_to_module(row)
    assert out["created_at"] == ts.isoformat()
    assert out["updated_at"] == ts.isoformat()


def test_migration_005_creates_settings_table():
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent / "migrations" / "005_portal_settings.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS portal_settings" in sql
