"""审计接口 /api/admin/audit：分页游标、筛选参数透传、limit 钳制、日期解析、门禁。
不连数据库：db.list_audit_logs 换成记录参数的桩。"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import AuthInfo, require_auth
from app.main import app


def _auth(perms=("*",)):
    return AuthInfo(matched=True, matched_by="test",
                    user={"id": "u", "display_name": "测试管理员", "email": "a@zestrade.com",
                          "mobile": "", "status": "active"},
                    roles=[], permissions=list(perms), contacts={})


def _log(i):
    return {"id": i, "actor": "张三", "action": "update", "target_type": "module",
            "target_id": str(i), "detail": {}, "created_at": "2026-09-11T10:00:00+08:00"}


@pytest.fixture
def client(monkeypatch):
    calls = []

    def fake_list(limit, **kw):
        calls.append({"limit": limit, **kw})
        return [_log(100 - i) for i in range(fake_list.rows)]

    fake_list.rows = 3
    monkeypatch.setattr(main.db, "list_audit_logs", fake_list)
    app.dependency_overrides[require_auth] = lambda: _auth()
    c = TestClient(app)
    c.calls, c.fake = calls, fake_list
    yield c
    app.dependency_overrides.clear()


def test_default_page_no_more(client):
    r = client.get("/api/admin/audit")
    assert r.status_code == 200
    body = r.json()
    assert [l["id"] for l in body["logs"]] == [100, 99, 98]
    assert body["next_before_id"] is None          # 不足一页 = 没有更多
    assert client.calls[0]["limit"] == 50
    assert client.calls[0]["before_id"] is None


def test_full_page_returns_cursor(client):
    client.fake.rows = 50
    body = client.get("/api/admin/audit?limit=50").json()
    assert len(body["logs"]) == 50
    assert body["next_before_id"] == body["logs"][-1]["id"]


def test_filters_passed_through(client):
    client.get("/api/admin/audit?actor=%20张%20&action=delete&target_type=module"
               "&since=2026-09-01&until=2026-09-11&before_id=80&limit=20")
    c = client.calls[0]
    assert c["limit"] == 20 and c["before_id"] == 80
    assert c["actor"] == "张" and c["action"] == "delete" and c["target_type"] == "module"
    assert c["since"] == datetime(2026, 9, 1)
    assert c["until"] == datetime(2026, 9, 12)       # until 含当天整天：取次日零点作右开区间


def test_empty_filters_become_none(client):
    client.get("/api/admin/audit?actor=&action=&target_type=")
    c = client.calls[0]
    assert c["actor"] is None and c["action"] is None and c["target_type"] is None


def test_limit_clamped(client):
    client.get("/api/admin/audit?limit=9999")
    assert client.calls[0]["limit"] == main.AUDIT_PAGE_MAX
    client.get("/api/admin/audit?limit=0")
    assert client.calls[1]["limit"] == 1


def test_bad_date_is_400(client):
    r = client.get("/api/admin/audit?since=2026/09/01")
    assert r.status_code == 400
    assert "since" in r.json()["detail"]
    assert client.calls == []


def test_requires_manage_permission(client):
    app.dependency_overrides[require_auth] = lambda: _auth(perms=("read:sku",))
    assert client.get("/api/admin/audit").status_code == 403
