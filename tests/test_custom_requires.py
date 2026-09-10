"""门户自定义准入规则接口（/api/admin/custom-requires）：写法校验、与 RBAC 目录对账、
删除级联计数、门禁。不连数据库：db 层函数 monkeypatch；目录用 monkeypatch 的 get_catalog。"""

import pytest
from fastapi.testclient import TestClient

from app import main, rbac_catalog
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
    """内存版 custom_requires + 引用计数。"""

    def __init__(self):
        self.items = {}      # code -> note
        self.module_refs = {"pm_system:*": 2}
        self.ann_refs = {"pm_system:*": 1}
        self.retired = []
        self.logs = []

    def list_custom_requires(self):
        return [{"code": c, "note": n, "created_by": "", "created_at": None,
                 "refs": {"modules": self.module_refs.get(c, 0),
                          "announcements": self.ann_refs.get(c, 0)}}
                for c, n in self.items.items()]

    def add_custom_require(self, code, note="", created_by=""):
        self.items.setdefault(code, note)
        return next(c for c in self.list_custom_requires() if c["code"] == code)

    def delete_custom_require(self, code):
        if code not in self.items:
            return None
        del self.items[code]
        return {"modules": self.module_refs.pop(code, 0), "announcements": self.ann_refs.pop(code, 0)}

    def retire_custom_require(self, code):
        self.retired.append(code)
        self.items.pop(code, None)

    def log_action(self, *a, **k):
        self.logs.append((a, k))


@pytest.fixture
def fake(monkeypatch):
    f = _FakeDB()
    for name in ("list_custom_requires", "add_custom_require", "delete_custom_require",
                 "retire_custom_require", "log_action"):
        monkeypatch.setattr(main.db, name, getattr(f, name))
    return f


@pytest.fixture
def catalog(monkeypatch):
    codes = ["pm_system:read:sku", "platform:read:users"]

    async def _get():
        return {"permissions": [{"code": c, "system": c.split(":")[0]} for c in codes]}

    monkeypatch.setattr(rbac_catalog, "get_catalog", _get)
    return codes


@pytest.fixture
def client(fake, catalog):
    app.dependency_overrides[require_auth] = lambda: _auth()
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── 门禁 ──


def test_requires_manage_permission(fake, catalog):
    app.dependency_overrides[require_auth] = lambda: _auth(perms=["pm_system:read:sku"])
    try:
        assert TestClient(app).get("/api/admin/custom-requires").status_code == 403
    finally:
        app.dependency_overrides.clear()


# ── 新增 ──


def test_add_prefix_and_wildcard(client, fake):
    assert client.post("/api/admin/custom-requires", json={"code": "mrp_system"}).status_code == 200
    r = client.post("/api/admin/custom-requires", json={"code": " mrp_system:* "})
    assert r.status_code == 200 and r.json()["code"] == "mrp_system:*"   # 首尾空白被裁掉
    assert set(fake.items) == {"mrp_system", "mrp_system:*"}
    assert fake.logs and fake.logs[-1][0][1:3] == ("create", "custom_require")


@pytest.mark.parametrize("bad", ["*", "a:b:*", "pm*", "", "a b", "x" * 101])
def test_add_rejects_bad_forms(client, bad):
    assert client.post("/api/admin/custom-requires", json={"code": bad}).status_code == 422


def test_add_code_already_in_rbac_catalog_is_400(client, fake):
    r = client.post("/api/admin/custom-requires", json={"code": "pm_system:read:sku"})
    assert r.status_code == 400 and "RBAC" in r.json()["detail"]
    assert not fake.items


def test_add_is_idempotent(client, fake):
    client.post("/api/admin/custom-requires", json={"code": "mrp_system", "note": "第一次"})
    r = client.post("/api/admin/custom-requires", json={"code": "mrp_system", "note": "第二次"})
    assert r.status_code == 200 and r.json()["note"] == "第一次"


def test_add_works_when_catalog_unavailable(client, fake, monkeypatch):
    async def _boom():
        raise RuntimeError("catalog down")
    monkeypatch.setattr(rbac_catalog, "get_catalog", _boom)
    assert client.post("/api/admin/custom-requires", json={"code": "pm_system:*"}).status_code == 200


# ── 列表与对账 ──


def test_list_returns_refs(client, fake):
    fake.items["pm_system:*"] = ""
    items = client.get("/api/admin/custom-requires").json()["items"]
    assert items == [{"code": "pm_system:*", "note": "", "created_by": "", "created_at": None,
                      "refs": {"modules": 2, "announcements": 1}}]


def test_list_retires_codes_now_registered_in_rbac(client, fake):
    """RBAC 后来登记了同名完整码：自定义条目自动归还目录，且不动引用它的应用/公告。"""
    fake.items.update({"platform:read:users": "", "mrp_system": ""})
    items = client.get("/api/admin/custom-requires").json()["items"]
    assert [i["code"] for i in items] == ["mrp_system"]
    assert fake.retired == ["platform:read:users"]


# ── 删除 ──


def test_delete_cascades_and_reports_counts(client, fake):
    fake.items["pm_system:*"] = ""
    r = client.delete("/api/admin/custom-requires/pm_system:*")
    assert r.status_code == 200
    assert r.json()["removed_from"] == {"modules": 2, "announcements": 1}
    assert "pm_system:*" not in fake.items
    assert fake.logs[-1][1]["detail"]["removed_from"] == {"modules": 2, "announcements": 1}


def test_delete_unknown_is_404(client):
    assert client.delete("/api/admin/custom-requires/nope").status_code == 404
