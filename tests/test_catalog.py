"""RBAC 权限目录客户端：bypass 桩目录 / TTL 缓存 / ETag 304 续期 / 失败重试与旧缓存兜底。
不发真实网络请求：HTTP 客户端换成按脚本出结果的桩。"""

import asyncio

import pytest

from app import auth, rbac_catalog


@pytest.fixture(autouse=True)
def _reset_cache():
    rbac_catalog._cache = None
    rbac_catalog._etag = None
    rbac_catalog._fresh_until = 0.0
    yield
    rbac_catalog._cache = None
    rbac_catalog._etag = None
    rbac_catalog._fresh_until = 0.0


class _Resp:
    def __init__(self, status_code, payload=None, etag=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"etag": etag} if etag else {}

    def json(self):
        return self._payload


class _Client:
    """每次 get 按顺序弹出一个预置结果（Exception 则抛出）。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self.last_headers = None

    async def get(self, url, headers=None):
        self.calls += 1
        self.last_headers = headers or {}
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _live(monkeypatch, client):
    monkeypatch.setattr(auth, "AUTH_BYPASS", False)
    monkeypatch.setattr(auth, "RBAC_CLIENT_ID", "cid")
    monkeypatch.setattr(auth, "RBAC_CLIENT_SECRET", "sec")
    monkeypatch.setattr(auth, "_http_client", client)


def test_bypass_returns_stub():
    assert asyncio.run(rbac_catalog.get_catalog()) is rbac_catalog._BYPASS_CATALOG


def test_fetch_then_cached_within_ttl(monkeypatch):
    c = _Client([_Resp(200, {"permissions": []}, etag='W/"x"')])
    _live(monkeypatch, c)
    d1 = asyncio.run(rbac_catalog.get_catalog())
    d2 = asyncio.run(rbac_catalog.get_catalog())  # TTL 内不回源
    assert d1 is d2 and c.calls == 1


def test_etag_304_renews_cache(monkeypatch):
    c = _Client([_Resp(200, {"v": 1}, etag='W/"x"'), _Resp(304)])
    _live(monkeypatch, c)
    asyncio.run(rbac_catalog.get_catalog())
    rbac_catalog._fresh_until = 0.0  # 强制过期，触发带 If-None-Match 回源
    assert asyncio.run(rbac_catalog.get_catalog()) == {"v": 1}
    assert c.calls == 2 and c.last_headers.get("If-None-Match") == 'W/"x"'


def test_failure_falls_back_to_stale_cache(monkeypatch, caplog):
    c = _Client([_Resp(200, {"v": 1}), RuntimeError("502"), RuntimeError("502")])
    _live(monkeypatch, c)
    asyncio.run(rbac_catalog.get_catalog())
    rbac_catalog._fresh_until = 0.0
    with caplog.at_level("WARNING", logger="app.rbac_catalog"):
        assert asyncio.run(rbac_catalog.get_catalog()) == {"v": 1}  # 重试两次失败后用旧缓存
    assert any("旧缓存" in r.getMessage() for r in caplog.records)


def test_failure_without_cache_raises(monkeypatch):
    c = _Client([_Resp(502), _Resp(502)])
    _live(monkeypatch, c)
    with pytest.raises(RuntimeError):
        asyncio.run(rbac_catalog.get_catalog())
