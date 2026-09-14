"""后台公告分页与筛选（/api/admin/announcements）：与审计同一套游标分页；
状态/关键字/日期筛选的 SQL 片段用纯函数直接测；接口用 monkeypatch 的内存版。"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.auth import AuthInfo, require_auth
from app.main import app


# ── announcement_filters 纯函数 ──

def test_filters_empty():
    assert db.announcement_filters() == ([], [])


def test_filters_status_keyword_dates_cursor():
    where, params = db.announcement_filters(
        status="expired", q="维护", since=datetime(2026, 9, 1), until=datetime(2026, 9, 15), before_id=80)
    assert where[0] == "id < %s" and params[0] == 80
    assert "ends_at < now()" in where[1]
    assert where[2] == "content ILIKE %s" and params[1] == "%维护%"
    assert where[3] == "created_at >= %s" and where[4] == "created_at < %s"
    assert params[2:] == [datetime(2026, 9, 1), datetime(2026, 9, 15)]


@pytest.mark.parametrize("status", ["all", "", None, "bogus"])
def test_filters_unknown_status_means_all(status):
    assert db.announcement_filters(status=status) == ([], [])


def test_filters_live_is_active_or_pending():
    where, _ = db.announcement_filters(status="live")
    assert db.ANN_STATUS_SQL["active"] in where[0] and db.ANN_STATUS_SQL["pending"] in where[0]


# ── 接口 ──

class _FakeDB:
    def __init__(self):
        self.calls = []
        self.anns = [{"id": i, "content": f"a{i}", "level": "info", "starts_at": None, "ends_at": None,
                      "requires": [], "enabled": True, "created_at": None} for i in range(1, 121)]

    def list_announcements_page(self, limit=50, **f):
        self.calls.append((limit, f))
        rows = [a for a in self.anns if f.get("before_id") is None or a["id"] < f["before_id"]]
        return sorted(rows, key=lambda a: -a["id"])[:limit]

    def count_announcements(self):
        return {"total": len(self.anns), "active": len(self.anns), "pending": 0, "expired": 0, "disabled": 0}


@pytest.fixture
def fake(monkeypatch):
    f = _FakeDB()
    for name in ("list_announcements_page", "count_announcements"):
        monkeypatch.setattr(main.db, name, getattr(f, name))
    return f


@pytest.fixture
def client(fake):
    app.dependency_overrides[require_auth] = lambda: AuthInfo(
        matched=True, matched_by="test",
        user={"id": "u", "display_name": "管理员", "email": "a@zestrade.com", "mobile": "", "status": "active"},
        roles=[], permissions=["*"], contacts={})
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_pages_through_all_with_cursor(client, fake):
    seen, cursor = [], None
    for _ in range(10):
        url = "/api/admin/announcements" + (f"?before_id={cursor}" if cursor else "")
        d = client.get(url).json()
        seen += [a["id"] for a in d["announcements"]]
        cursor = d["next_before_id"]
        if cursor is None:
            break
    assert seen == list(range(120, 0, -1)), "三页 50+50+20 翻完 120 条，不重不漏"
    assert d["counts"]["total"] == 120


def test_default_page_is_50_and_filters_passed_through(client, fake):
    d = client.get("/api/admin/announcements?status=expired&q=%20维护%20&since=2026-09-01&until=2026-09-14").json()
    assert len(d["announcements"]) == 50 and d["next_before_id"] == 71
    limit, f = fake.calls[-1]
    assert limit == 50 and f["status"] == "expired" and f["q"] == "维护"
    assert f["since"] == datetime(2026, 9, 1) and f["until"] == datetime(2026, 9, 15), "until 含当天整天"


def test_limit_capped_and_bad_date_400(client, fake):
    d = client.get("/api/admin/announcements?limit=999").json()
    assert fake.calls[-1][0] == 200, "单次最多 200"
    assert len(d["announcements"]) == 120 and d["next_before_id"] is None
    assert client.get("/api/admin/announcements?since=2026/09/01").status_code == 400


def test_requires_manage_permission(fake):
    app.dependency_overrides[require_auth] = lambda: AuthInfo(
        matched=True, matched_by="test", user={"id": "u", "display_name": "员工", "email": "e@zestrade.com",
        "mobile": "", "status": "active"}, roles=[], permissions=["pm_system:read:sku"], contacts={})
    try:
        assert TestClient(app).get("/api/admin/announcements").status_code == 403
    finally:
        app.dependency_overrides.clear()
