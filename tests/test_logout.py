"""/logout 路由：演示模式（AUTH_BYPASS）回首页并带提示参数，不跳 Keycloak；
正式模式经 oauth2-proxy sign_out 级联登出，returnTo 透传为 rd；站外 returnTo 丢弃。"""

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app


@pytest.fixture
def client():
    # 不用 with：不触发 lifespan，不连数据库
    yield TestClient(app, base_url="https://portal.zestrade.com")


def test_bypass_logout_goes_home_with_notice(client):
    assert auth.AUTH_BYPASS is True
    resp = client.get("/logout?returnTo=/admin", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/?demo_logout=1"


def test_real_logout_uses_proxy_sign_out(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_BYPASS", False)
    monkeypatch.setattr(
        auth, "OAUTH2_PROXY_SIGN_OUT_URL", "https://portal.zestrade.com/oauth2/sign_out"
    )
    resp = client.get(
        "/logout?returnTo=https://portal.zestrade.com/admin", follow_redirects=False
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == (
        "https://portal.zestrade.com/oauth2/sign_out"
        "?rd=https%3A%2F%2Fportal.zestrade.com%2Fadmin"
    )


def test_real_logout_drops_external_return_to(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_BYPASS", False)
    monkeypatch.setattr(
        auth, "OAUTH2_PROXY_SIGN_OUT_URL", "https://portal.zestrade.com/oauth2/sign_out"
    )
    resp = client.get("/logout?returnTo=https://evil.com/", follow_redirects=False)
    assert resp.headers["location"] == "https://portal.zestrade.com/oauth2/sign_out"


def test_real_logout_clears_cached_token(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_BYPASS", False)
    monkeypatch.setattr(auth, "OAUTH2_PROXY_SIGN_OUT_URL", "https://p/oauth2/sign_out")
    auth._set_cached("tok", auth._BYPASS_AUTH)
    client.get("/logout", headers={"X-Auth-Request-Access-Token": "tok"}, follow_redirects=False)
    assert auth._get_cached("tok") is None
