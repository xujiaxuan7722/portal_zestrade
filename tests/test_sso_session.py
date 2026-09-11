"""门户自己连共享 oauth2-proxy（sso）：会话 cookie → 令牌、登录入口、页面未登录自动跳 sso。
不发真实网络请求：auth._http_client 换成桩。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.auth import AuthInfo, require_auth
from app.main import app

SSO = "https://sso.zestrade.com"


class _Resp:
    def __init__(self, status, headers=None):
        self.status_code, self.headers = status, headers or {}


class _Client:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    async def get(self, url, headers=None):
        self.calls.append((url, headers))
        return self.resp


def _req(cookies=None, headers=None, path="/", query=""):
    return SimpleNamespace(
        cookies=cookies or {}, headers=headers or {},
        url=SimpleNamespace(scheme="http", netloc="127.0.0.1:8201", path=path, query=query),
    )


@pytest.fixture(autouse=True)
def _sso(monkeypatch):
    monkeypatch.setattr(auth, "OAUTH2_PROXY_URL", SSO)
    auth._session_token_cache.clear()
    yield
    auth._session_token_cache.clear()


# ── 会话 cookie → 令牌 ──


def test_session_exchange_returns_token_and_forwards_only_proxy_cookies(monkeypatch):
    client = _Client(_Resp(202, {"X-Auth-Request-Access-Token": "tok123"}))
    monkeypatch.setattr(auth, "_http_client", client)
    req = _req(cookies={"_oauth2_proxy": "abc", "_oauth2_proxy_1": "def", "other": "x"})
    assert asyncio.run(auth.token_from_session(req)) == "tok123"
    url, headers = client.calls[0]
    assert url == f"{SSO}/oauth2/auth"
    assert headers["Cookie"] == "_oauth2_proxy=abc; _oauth2_proxy_1=def"


def test_session_exchange_cached(monkeypatch):
    client = _Client(_Resp(202, {"X-Auth-Request-Access-Token": "tok"}))
    monkeypatch.setattr(auth, "_http_client", client)
    req = _req(cookies={"_oauth2_proxy": "abc"})
    asyncio.run(auth.token_from_session(req))
    asyncio.run(auth.token_from_session(req))
    assert len(client.calls) == 1


def test_session_exchange_not_logged_in(monkeypatch):
    client = _Client(_Resp(401))
    monkeypatch.setattr(auth, "_http_client", client)
    assert asyncio.run(auth.token_from_session(_req(cookies={"_oauth2_proxy": "abc"}))) == ""


def test_session_exchange_skipped_without_cookie_or_config(monkeypatch):
    client = _Client(_Resp(202, {"X-Auth-Request-Access-Token": "tok"}))
    monkeypatch.setattr(auth, "_http_client", client)
    assert asyncio.run(auth.token_from_session(_req())) == ""
    monkeypatch.setattr(auth, "OAUTH2_PROXY_URL", "")
    assert asyncio.run(auth.token_from_session(_req(cookies={"_oauth2_proxy": "abc"}))) == ""
    assert client.calls == []


def test_header_token_takes_precedence(monkeypatch):
    client = _Client(_Resp(202, {"X-Auth-Request-Access-Token": "from-session"}))
    monkeypatch.setattr(auth, "_http_client", client)
    req = _req(cookies={"_oauth2_proxy": "abc"}, headers={"X-Auth-Request-Access-Token": "from-header"})
    assert asyncio.run(auth.resolve_user_jwt(req)) == "from-header"
    assert client.calls == []


def test_public_url_uses_forwarded_headers():
    req = _req(headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "portal.zestrade.com"},
               path="/admin", query="x=1")
    assert auth.public_url(req) == "https://portal.zestrade.com/admin?x=1"
    assert auth.public_url(req, "/") == "https://portal.zestrade.com/"
    assert auth.public_url(_req(path="/a")) == "http://127.0.0.1:8201/a"


# ── 路由 ──


def _auth_info():
    return AuthInfo(matched=True, matched_by="unionid",
                    user={"id": "u", "display_name": "徐佳轩", "email": None, "mobile": "", "status": "active"},
                    roles=[{"name": "member"}], permissions=["read:items"], contacts={})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_BYPASS", False)
    yield TestClient(app, base_url="https://portal.zestrade.com")
    app.dependency_overrides.clear()


def test_login_redirects_to_sso_with_public_return(client):
    r = client.get("/login?returnTo=/admin", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == f"{SSO}/oauth2/start?rd=https%3A%2F%2Fportal.zestrade.com%2Fadmin"


def test_login_drops_external_return(client):
    r = client.get("/login?returnTo=https://evil.com/", follow_redirects=False)
    assert r.headers["location"] == f"{SSO}/oauth2/start?rd=https%3A%2F%2Fportal.zestrade.com%2F"


def test_login_in_bypass_goes_home(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_BYPASS", True)
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"


def test_page_redirects_to_sso_when_not_logged_in(client, monkeypatch):
    monkeypatch.setattr(auth, "_http_client", _Client(_Resp(401)))
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == f"{SSO}/oauth2/start?rd=https%3A%2F%2Fportal.zestrade.com%2F"


def test_page_served_when_session_valid(client, monkeypatch):
    monkeypatch.setattr(auth, "_http_client", _Client(_Resp(202, {"X-Auth-Request-Access-Token": "tok"})))

    async def fake_get_auth_info(token):
        return _auth_info() if token == "tok" else None
    monkeypatch.setattr(main, "get_auth_info", fake_get_auth_info)
    r = client.get("/admin", cookies={"_oauth2_proxy": "abc"}, follow_redirects=False)
    assert r.status_code == 200 and "admin" in r.text.lower()


def test_page_served_without_sso_config(client, monkeypatch):
    """未配置 OAUTH2_PROXY_URL：页面照出（靠代理层拦截），API 仍 401。"""
    monkeypatch.setattr(auth, "OAUTH2_PROXY_URL", "")
    assert client.get("/", follow_redirects=False).status_code == 200


def test_api_401_without_session(client, monkeypatch):
    monkeypatch.setattr(auth, "_http_client", _Client(_Resp(401)))
    monkeypatch.setattr(auth, "RBAC_CLIENT_ID", "id")
    monkeypatch.setattr(auth, "RBAC_CLIENT_SECRET", "secret")
    assert client.get("/api/me", cookies={"_oauth2_proxy": "abc"}).status_code == 401


def test_logout_uses_sso_sign_out_with_public_rd(client, monkeypatch):
    monkeypatch.setattr(auth, "OAUTH2_PROXY_SIGN_OUT_URL", f"{SSO}/oauth2/sign_out")
    monkeypatch.setattr(auth, "_http_client", _Client(_Resp(401)))
    r = client.get("/logout?returnTo=/", follow_redirects=False)
    assert r.headers["location"] == f"{SSO}/oauth2/sign_out?rd=https%3A%2F%2Fportal.zestrade.com%2F"
