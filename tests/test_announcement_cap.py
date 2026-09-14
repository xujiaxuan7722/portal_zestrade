"""公告条数上限：库里最多 ANNOUNCEMENT_CAP 条，新增超出时自动删除（先删已失效的，再删最早的），
并记一条汇总审计。不连数据库：select_prunable 是纯函数直接测；接口用 monkeypatch 的内存版。"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.auth import AuthInfo, require_auth
from app.main import app

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _ann(i, enabled=True, ends=None):
    return {"id": i, "enabled": enabled, "ends_at": ends}


def test_prunable_nothing_under_cap():
    assert db.select_prunable([_ann(i) for i in range(1, 51)], cap=50, now=NOW) == []


def test_prunable_prefers_dead_then_oldest():
    anns = [_ann(i) for i in range(1, 54)]                         # 53 条，超 3
    anns[9] = _ann(10, ends=NOW - timedelta(days=1))              # 已过期
    anns[29] = _ann(30, enabled=False)                            # 已停用
    anns[49] = _ann(50, ends=NOW + timedelta(days=1))             # 未过期，不算失效
    assert db.select_prunable(anns, cap=50, now=NOW) == [10, 30, 1]


def test_prunable_accepts_iso_strings():
    anns = [_ann(i) for i in range(1, 53)]
    anns[40] = {"id": 41, "enabled": True, "ends_at": (NOW - timedelta(hours=1)).isoformat()}
    assert db.select_prunable(anns, cap=50, now=NOW) == [41, 1]


class _FakeDB:
    def __init__(self):
        self.anns = [{"id": i, "content": f"a{i}", "level": "info", "starts_at": None, "ends_at": None,
                      "requires": [], "enabled": True, "created_at": None} for i in range(1, 51)]
        self.logs = []

    def create_announcement(self, data):
        a = {"id": max(x["id"] for x in self.anns) + 1, **data, "created_at": None}
        self.anns.append(a)
        return a

    def list_announcements(self):
        return list(self.anns)

    def prune_announcements(self, cap=db.ANNOUNCEMENT_CAP):
        ids = db.select_prunable(self.anns, cap, now=NOW)
        self.anns = [a for a in self.anns if a["id"] not in ids]
        return ids

    def log_action(self, *a, **k):
        self.logs.append((a, k))


@pytest.fixture
def fake(monkeypatch):
    f = _FakeDB()
    for name in ("create_announcement", "list_announcements", "prune_announcements", "log_action"):
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


def test_create_over_cap_prunes_oldest_and_audits(client, fake):
    r = client.post("/api/admin/announcements", json={"content": "第 51 条"})
    assert r.status_code == 200
    assert len(fake.anns) == 50
    assert [a["id"] for a in fake.anns][0] == 2, "最早的 1 号被删"
    assert fake.anns[-1]["content"] == "第 51 条"
    actions = [(a[1], a[2], a[3]) for a, _ in fake.logs]
    assert actions == [("create", "announcement", 51), ("delete", "announcement", "auto")]
    args, kw = fake.logs[1]
    assert (kw.get("detail") or args[4]) == {"auto": True, "cap": 50, "ids": [1]}


def test_create_under_cap_no_prune(client, fake):
    fake.anns = fake.anns[:10]
    client.post("/api/admin/announcements", json={"content": "x"})
    assert len(fake.anns) == 11
    assert [a[1] for a, _ in fake.logs] == ["create"]
