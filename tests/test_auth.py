"""auth 层单测：权限判断 / JWT 提取 / 缓存（正/负/淘汰）/ fail-closed / returnTo 白名单。
不发真实网络请求：RBAC 调用一律把 _http_client 换成桩。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import auth


@pytest.fixture(autouse=True)
def _clean_cache():
    auth._auth_cache.clear()
    yield
    auth._auth_cache.clear()


def _request(headers=None, netloc="portal.zestrade.com"):
    return SimpleNamespace(
        headers=headers or {},
        url=SimpleNamespace(netloc=netloc, scheme="https"),
    )


def _fake_clock(monkeypatch, start=1000.0):
    now = [start]
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: now[0]))
    return now


# ── 权限 / JWT 提取 / returnTo ──


def test_has_permission_wildcards():
    assert auth.has_permission(["*"], "write:modules")
    assert auth.has_permission(["write:modules"], "write:modules")
    assert auth.has_permission(["write:*"], "write:modules")
    assert not auth.has_permission(["read:modules"], "write:modules")
    assert not auth.has_permission([], "write:modules")


def test_extract_user_jwt_priority():
    r = _request({
        "X-Auth-Request-Access-Token": "a",
        "X-Forwarded-Access-Token": "b",
        "Authorization": "Bearer c",
    })
    assert auth.extract_user_jwt(r) == "a"
    assert auth.extract_user_jwt(_request({"X-Forwarded-Access-Token": "b"})) == "b"
    assert auth.extract_user_jwt(_request({"Authorization": "Bearer c"})) == "c"
    assert auth.extract_user_jwt(_request({"Authorization": "Basic xxx"})) == ""
    assert auth.extract_user_jwt(_request()) == ""


def test_sanitize_return_to():
    r = _request()
    assert auth.sanitize_return_to(r, "/admin") == "/admin"
    assert auth.sanitize_return_to(r, "//evil.com/x") is None
    assert (
        auth.sanitize_return_to(r, "https://portal.zestrade.com/x")
        == "https://portal.zestrade.com/x"
    )
    assert auth.sanitize_return_to(r, "https://evil.com/x") is None
    assert auth.sanitize_return_to(r, "javascript:alert(1)") is None
    assert auth.sanitize_return_to(r, None) is None


# ── 缓存 ──


def test_cache_positive_and_expiry(monkeypatch):
    now = _fake_clock(monkeypatch)
    auth._set_cached("tok", auth._BYPASS_AUTH)
    assert auth._get_cached("tok").data is auth._BYPASS_AUTH
    now[0] += auth.CACHE_TTL + 1
    assert auth._get_cached("tok") is None


def test_negative_cache_distinguished_from_miss(monkeypatch):
    now = _fake_clock(monkeypatch)
    auth._set_cached("tok", None, auth.NEG_CACHE_TTL)
    entry = auth._get_cached("tok")
    assert entry is not None and entry.data is None  # 负缓存命中 ≠ 无缓存
    now[0] += auth.NEG_CACHE_TTL + 1
    assert auth._get_cached("tok") is None


def test_cache_sweep_caps_entries(monkeypatch):
    _fake_clock(monkeypatch)
    monkeypatch.setattr(auth, "CACHE_MAX_ENTRIES", 10)
    for i in range(30):
        auth._set_cached(f"tok{i}", None, ttl=100)
    assert len(auth._auth_cache) <= 10


# ── get_auth_info（RBAC 调用桩）──


class _StubResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _StubClient:
    def __init__(self, result):
        self._result = result

    async def post(self, *args, **kwargs):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _with_client(monkeypatch, result):
    monkeypatch.setattr(auth, "_http_client", _StubClient(result))


_MATCHED = {
    "matched": True,
    "matched_by": "email",
    "user": {"id": "u1", "display_name": "测试", "email": "t@zestrade.com"},
    "roles": [{"id": "r1", "name": "finance", "source": "rbac"}],
    "permissions": ["read:*"],
    "contacts": {},
}


def test_get_auth_info_success_then_served_from_cache(monkeypatch):
    _with_client(monkeypatch, _StubResp(200, _MATCHED))
    info = asyncio.run(auth.get_auth_info("tok"))
    assert info.matched and info.roles[0]["name"] == "finance"
    # 命中正缓存：RBAC 随后故障也不影响 TTL 内的已认证用户
    _with_client(monkeypatch, RuntimeError("rbac down"))
    assert asyncio.run(auth.get_auth_info("tok")) is info


def test_get_auth_info_unmatched_user_is_rejected(monkeypatch):
    _with_client(monkeypatch, _StubResp(200, {"matched": False}))
    assert asyncio.run(auth.get_auth_info("tok")) is None


def test_get_auth_info_rbac_exception_logs_and_negcaches(monkeypatch, caplog):
    _with_client(monkeypatch, RuntimeError("connection refused"))
    with caplog.at_level("WARNING", logger="app.auth"):
        assert asyncio.run(auth.get_auth_info("tok")) is None
    assert any("RBAC" in r.getMessage() for r in caplog.records)
    entry = auth._get_cached("tok")
    assert entry is not None and entry.data is None


def test_get_auth_info_non200_logs_and_negcaches(monkeypatch, caplog):
    _with_client(monkeypatch, _StubResp(503))
    with caplog.at_level("WARNING", logger="app.auth"):
        assert asyncio.run(auth.get_auth_info("tok")) is None
    assert any("503" in r.getMessage() for r in caplog.records)
    entry = auth._get_cached("tok")
    assert entry is not None and entry.data is None


# ── require_auth：bypass / fail-closed ──


def test_require_auth_bypass_returns_test_user():
    assert asyncio.run(auth.require_auth(_request())) is auth._BYPASS_AUTH


def test_require_auth_fail_closed_when_rbac_unconfigured(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_BYPASS", False)
    monkeypatch.setattr(auth, "RBAC_CLIENT_ID", "")
    monkeypatch.setattr(auth, "RBAC_CLIENT_SECRET", "")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(auth.require_auth(_request()))
    assert ei.value.status_code == 401


def test_require_auth_rejects_unmatched_token(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_BYPASS", False)
    monkeypatch.setattr(auth, "RBAC_CLIENT_ID", "cid")
    monkeypatch.setattr(auth, "RBAC_CLIENT_SECRET", "sec")
    _with_client(monkeypatch, _StubResp(200, {"matched": False}))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(auth.require_auth(_request({"Authorization": "Bearer x"})))
    assert ei.value.status_code == 401
