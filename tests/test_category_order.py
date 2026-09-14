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
        self.colors = {}
        self.logs = []

    def get_category_colors(self):
        return dict(self.colors)

    def set_category_colors(self, colors):
        self.colors = dict(colors)
        return dict(colors)

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
    for name in ("get_category_order", "set_category_order", "get_category_colors",
                 "set_category_colors", "list_modules", "log_action"):
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
    fake.colors = {"电商运营": "red"}
    body = client.get("/api/modules").json()
    assert body["category_order"] == ["客户销售", "电商运营"]
    assert body["category_colors"] == {"电商运营": "red"}
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


def test_colors_put_cleans_validates_and_audits(client, fake):
    r = client.put("/api/admin/category-colors", json={"colors": {" 电商运营 ": "red", "": "blue", "未分类": "cyan"}})
    assert r.status_code == 200
    assert r.json()["colors"] == {"电商运营": "red", "未分类": "cyan"}
    assert client.get("/api/admin/category-colors").json()["colors"] == fake.colors
    (args, kw), = fake.logs
    assert args[1:3] == ("update", "category")
    assert kw["detail"] == {"colors": {"电商运营": "red", "未分类": "cyan"}}


@pytest.mark.parametrize("bad", [{"电商运营": "pink"}, {"电商运营": "#ff0000"}, {"x" * 51: "red"}])
def test_colors_put_rejects_bad_values(client, bad):
    assert client.put("/api/admin/category-colors", json={"colors": bad}).status_code == 422


def test_colors_require_manage_permission(fake):
    app.dependency_overrides[require_auth] = lambda: _auth(perms=("pm_system:read:sku",))
    try:
        c = TestClient(app)
        assert c.get("/api/admin/category-colors").status_code == 403
        assert c.put("/api/admin/category-colors", json={"colors": {"a": "red"}}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_assign_missing_colors_is_distinct_and_stable():
    cats = ["电商运营", "供应链生产", "产品设计", "客户销售", "协同办公", "管理中心"]
    out = db.assign_missing_colors({}, cats)
    assert set(out) == set(cats)
    assert len(set(out.values())) == 6, "6 个分类应分到 6 种不同颜色"
    # 已配的不动；新分类取用得最少的色，再跑一次结果不变
    out2 = db.assign_missing_colors(out, cats + ["新分类"])
    assert {k: out2[k] for k in cats} == out
    assert out2["新分类"] not in out.values()
    assert db.assign_missing_colors(out2, cats + ["新分类"]) == out2
    # 超过 8 个分类才开始复用颜色，且复用的是用得最少的
    many = [f"c{i}" for i in range(10)]
    out3 = db.assign_missing_colors({}, many)
    assert max(list(out3.values()).count(k) for k in db.PALETTE_KEYS) == 2
