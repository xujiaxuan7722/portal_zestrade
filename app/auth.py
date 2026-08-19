"""
auth.py — 接入公司统一 RBAC（rbac.bogoo.ai）+ SSO（Keycloak，经 oauth2-proxy）

改自公司规范模板 fastapi_auth.py（rbac-sso-integration skill）。本项目按
Keycloak 链路启用；CF Access 那组实现保留在注释里，切换 IdP 时按注释操作。

本地开发：设 AUTH_BYPASS=true 绕过认证，返回全权限测试用户（上线前必须关闭）。
上线前必须配置 RBAC_CLIENT_ID / RBAC_CLIENT_SECRET（CF Zero Trust 的
Service Token，并需在 rbac.bogoo.ai 的 Application Policy 中放行）——
未配置且未开 bypass 时，所有请求一律判定为未认证（fail-closed）。
"""

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import Depends, HTTPException, Request

logger = logging.getLogger(__name__)

# ============================================================
# 配置
# ============================================================

RBAC_API_URL = os.getenv("RBAC_API_URL", "https://rbac.bogoo.ai")
RBAC_CLIENT_ID = os.getenv("RBAC_CLIENT_ID", "")
RBAC_CLIENT_SECRET = os.getenv("RBAC_CLIENT_SECRET", "")
CACHE_TTL = 300  # 权限缓存 5 分钟；权限变更不退出登录时最多延迟 5 分钟生效
NEG_CACHE_TTL = 15  # RBAC 调用失败（非 200/网络异常）的负缓存，故障时不至于每请求挂 10s 超时
CACHE_MAX_ENTRIES = 5000  # 缓存条目上限（oauth2-proxy 会定期刷新 token，条目会持续新增）

IDP = os.getenv("IDP", "keycloak").strip().lower()  # "keycloak" 或 "cf-access"
KEYCLOAK_LOGOUT_URL = os.getenv(
    "KEYCLOAK_LOGOUT_URL",
    "https://auth.duoweitree.com/realms/sso/protocol/openid-connect/logout",
)
# 本项目部署链路走 oauth2-proxy：登出必须经它的 sign_out 地址，才能同时清掉
# oauth2-proxy 会话 cookie 并级联 Keycloak 登出。只登出 Keycloak 的话，proxy
# 会话还活着，刷新页面即被放行（"退出了个寂寞"）——所以生产环境必配。
OAUTH2_PROXY_SIGN_OUT_URL = os.getenv("OAUTH2_PROXY_SIGN_OUT_URL", "")
# Keycloak 直连登出的兜底：新版 Keycloak 带 post_logout_redirect_uri 时要求
# 伴随 id_token_hint 或 client_id，应用拿不到 id_token（在 oauth2-proxy 手里），
# 故用 client_id 满足该要求
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "")

AUTH_BYPASS = os.getenv("AUTH_BYPASS", "false").strip().lower() in ("1", "true", "yes")


def _validate_idp_config() -> None:
    if IDP not in ("cf-access", "keycloak"):
        raise RuntimeError(f"IDP env must be 'cf-access' or 'keycloak', got: {IDP!r}")
    if IDP == "keycloak" and not KEYCLOAK_LOGOUT_URL:
        raise RuntimeError("IDP=keycloak requires KEYCLOAK_LOGOUT_URL env")
    # 防呆：AUTH_BYPASS 是全权限后门，只允许在"未配 RBAC 凭证"的本地开发环境使用；
    # 与生产凭证同时出现视为误配置，直接拒绝启动
    if AUTH_BYPASS and (RBAC_CLIENT_ID or RBAC_CLIENT_SECRET):
        raise RuntimeError(
            "AUTH_BYPASS=true 与 RBAC_CLIENT_ID/SECRET 同时配置——"
            "疑似在生产环境误开认证绕过，拒绝启动（本地开发请勿配置 RBAC 凭证）"
        )
    if (
        not AUTH_BYPASS
        and IDP == "keycloak"
        and not OAUTH2_PROXY_SIGN_OUT_URL
        and not KEYCLOAK_CLIENT_ID
    ):
        logger.warning(
            "未配置 OAUTH2_PROXY_SIGN_OUT_URL（推荐，经 oauth2-proxy 部署时必配）"
            "也未配置 KEYCLOAK_CLIENT_ID——登出将不彻底或被 Keycloak 拒绝跳转"
        )


_validate_idp_config()


# ============================================================
# 类型定义 — 对齐 /api/service/users/by-jwt 的响应结构
# ============================================================


@dataclass
class AuthInfo:
    matched: bool
    matched_by: Optional[str]
    user: Optional[dict]        # {id, display_name, email, mobile, status}
    roles: list[dict]           # [{id, name, source}, ...]
    permissions: list[str]      # ["admin:*", "read:users", ...]
    contacts: dict


@dataclass
class _CacheEntry:
    data: Optional[AuthInfo]  # None = 负缓存（RBAC 调用刚失败过，短时间内不再打）
    expires_at: float


# AUTH_BYPASS 模式下返回的测试用户（仅本地开发用）。
# 可用环境变量模拟不同角色的用户，测试可见性过滤：
#   AUTH_BYPASS_NAME=财务小王 AUTH_BYPASS_ROLES=finance ./run.sh
# 不设时默认为 admin 全权限用户；不同模拟用户（按邮箱区分）各有各的常用应用。
AUTH_BYPASS_NAME = os.getenv("AUTH_BYPASS_NAME", "开发测试用户")
AUTH_BYPASS_ROLES = [
    r.strip() for r in os.getenv("AUTH_BYPASS_ROLES", "admin").split(",") if r.strip()
]
AUTH_BYPASS_EMAIL = os.getenv(
    "AUTH_BYPASS_EMAIL", f"dev-{'-'.join(AUTH_BYPASS_ROLES)}@zestrade.com"
)
_BYPASS_AUTH = AuthInfo(
    matched=True,
    matched_by="bypass",
    user={
        "id": AUTH_BYPASS_EMAIL,
        "display_name": AUTH_BYPASS_NAME,
        "email": AUTH_BYPASS_EMAIL,
        "mobile": "",
        "status": "active",
    },
    roles=[{"id": f"dev-{r}", "name": r, "source": "bypass"} for r in AUTH_BYPASS_ROLES],
    permissions=["*"] if "admin" in AUTH_BYPASS_ROLES else [],
    contacts={},
)


# ============================================================
# 提取用户 JWT — 本项目走 Keycloak（oauth2-proxy）
# ============================================================


def extract_user_jwt(request: Request) -> str:
    """
    Keycloak 链路，按规范优先级取第一个非空：
      ① X-Auth-Request-Access-Token（oauth2-proxy 透传）
      ② X-Forwarded-Access-Token（oauth2-proxy 兜底）
      ③ Authorization: Bearer <token>
    """
    token = (
        request.headers.get("X-Auth-Request-Access-Token")
        or request.headers.get("X-Forwarded-Access-Token")
        or ""
    )
    if token:
        return token
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""

    # ──── 若切换为 CF Access，注释掉上面 Keycloak 块，启用下面这组 ────
    # import re
    # header = request.headers.get("Cf-Access-Jwt-Assertion") or ""
    # if header:
    #     return header
    # cookie_header = request.headers.get("Cookie") or ""
    # m = re.search(r'CF_Authorization=([^\s;]+)', cookie_header)
    # return m.group(1) if m else ""


# ============================================================
# 内存缓存
# ============================================================

_auth_cache: dict[str, _CacheEntry] = {}


def _cache_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def _get_cached(token: str) -> Optional[_CacheEntry]:
    """返回未过期的缓存条目；区分"没有缓存"（None）与"负缓存命中"（entry.data 为 None）。"""
    entry = _auth_cache.get(_cache_key(token))
    if entry and entry.expires_at > time.time():
        return entry
    if entry:
        _auth_cache.pop(_cache_key(token), None)
    return None


def _sweep_cache() -> None:
    """清掉过期条目；仍超上限（异常流量）时按过期时间丢最早的一半。"""
    now = time.time()
    for k in [k for k, v in _auth_cache.items() if v.expires_at <= now]:
        _auth_cache.pop(k, None)
    if len(_auth_cache) >= CACHE_MAX_ENTRIES:
        oldest = sorted(_auth_cache.items(), key=lambda kv: kv[1].expires_at)
        for k, _ in oldest[: len(_auth_cache) // 2]:
            _auth_cache.pop(k, None)


def _set_cached(token: str, data: Optional[AuthInfo], ttl: float = CACHE_TTL) -> None:
    if len(_auth_cache) >= CACHE_MAX_ENTRIES:
        _sweep_cache()
    _auth_cache[_cache_key(token)] = _CacheEntry(
        data=data, expires_at=time.time() + ttl
    )


def clear_auth_cache(jwt_token: str) -> None:
    """登出时调用：清掉本地缓存。"""
    if jwt_token:
        _auth_cache.pop(_cache_key(jwt_token), None)


# ============================================================
# 核心：用 JWT 换权限（带缓存）
# ============================================================


def _to_auth_info(data: dict[str, Any]) -> AuthInfo:
    return AuthInfo(
        matched=bool(data.get("matched")),
        matched_by=data.get("matched_by"),
        user=data.get("user"),
        roles=data.get("roles") or [],
        permissions=data.get("permissions") or [],
        contacts=data.get("contacts") or {},
    )


# 模块级复用（连接池 + keep-alive），避免每请求新建客户端
_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=10)
    return _http_client


async def close_http_client() -> None:
    """应用关闭时调用：优雅关闭到 RBAC 的 keep-alive 连接。"""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


async def get_auth_info(jwt_token: str) -> Optional[AuthInfo]:
    if not jwt_token:
        return None

    entry = _get_cached(jwt_token)
    if entry is not None:
        if entry.data is None:  # 负缓存：RBAC 刚失败过
            return None
        return entry.data if entry.data.matched else None

    try:
        resp = await _get_http_client().post(
            f"{RBAC_API_URL}/api/service/users/by-jwt",
            headers={
                "CF-Access-Client-Id": RBAC_CLIENT_ID,
                "CF-Access-Client-Secret": RBAC_CLIENT_SECRET,
                "Content-Type": "application/json",
            },
            json={"jwt": jwt_token},
        )
        if resp.status_code != 200:
            logger.warning(
                "RBAC by-jwt 返回 %s（%ss 内该 token 负缓存为未认证）",
                resp.status_code, NEG_CACHE_TTL,
            )
            _set_cached(jwt_token, None, NEG_CACHE_TTL)
            return None
        auth = _to_auth_info(resp.json())
    except Exception as exc:
        logger.warning(
            "RBAC 调用异常：%r（%ss 内该 token 负缓存为未认证）", exc, NEG_CACHE_TTL
        )
        _set_cached(jwt_token, None, NEG_CACHE_TTL)
        return None

    _set_cached(jwt_token, auth)
    return auth if auth.matched else None


# ============================================================
# 权限/角色判断
# ============================================================


def has_permission(permissions: list[str], required: str) -> bool:
    """权限码 "action:resource"，支持 "*" 与 "action:*" 通配。"""
    if "*" in permissions:
        return True
    if required in permissions:
        return True
    action = required.split(":")[0]
    return f"{action}:*" in permissions


def has_role(auth: AuthInfo, role_name: str) -> bool:
    return any(r.get("name") == role_name for r in auth.roles)


# ============================================================
# 登出 URL 构造
# ============================================================


def sanitize_return_to(request: Request, return_to: Optional[str]) -> Optional[str]:
    """
    防开放重定向：returnTo 只接受站内相对路径或本应用同源的绝对 URL，
    其余一律丢弃（丢弃后登出仍生效，只是不带跳回地址）。
    末端虽有 Keycloak 白名单 / oauth2-proxy whitelist-domains 兜底，这里做防御纵深。
    """
    if not return_to:
        return None
    # 相对路径；排除 "//evil.com" 这种协议相对形式
    if return_to.startswith("/") and not return_to.startswith("//"):
        return return_to
    parsed = urlsplit(return_to)
    if parsed.scheme in ("http", "https") and parsed.netloc == request.url.netloc:
        return return_to
    return None


def build_logout_url(
    request: Request,
    return_to: Optional[str] = None,
    id_token_hint: Optional[str] = None,
) -> str:
    """
    - oauth2-proxy 配置了 SIGN_OUT_URL → 直接跳它（returnTo 透传为 rd）
    - Keycloak → KEYCLOAK_LOGOUT_URL?post_logout_redirect_uri=<url>&id_token_hint=<jwt>
      ⚠ post_logout_redirect_uri 必须先在 Keycloak Client 的
        "Valid post logout redirect URIs" 里加白名单
    - CF Access → <本应用 origin>/cdn-cgi/access/logout?returnTo=<url>
    """
    params: dict[str, str] = {}
    if IDP == "cf-access":
        if return_to:
            params["returnTo"] = return_to
        base = f"{request.url.scheme}://{request.url.netloc}/cdn-cgi/access/logout"
    elif OAUTH2_PROXY_SIGN_OUT_URL:
        if return_to:
            params["rd"] = return_to
        base = OAUTH2_PROXY_SIGN_OUT_URL
    else:  # keycloak 直连（无 oauth2-proxy sign_out 地址时的兜底）
        if return_to:
            params["post_logout_redirect_uri"] = return_to
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        elif return_to and KEYCLOAK_CLIENT_ID:
            # 新版 Keycloak：post_logout_redirect_uri 必须伴随 id_token_hint 或 client_id
            params["client_id"] = KEYCLOAK_CLIENT_ID
        base = KEYCLOAK_LOGOUT_URL
    if params:
        return f"{base}?{urlencode(params)}"
    return base


# ============================================================
# FastAPI 依赖注入
# ============================================================


async def require_auth(request: Request) -> AuthInfo:
    if AUTH_BYPASS:
        return _BYPASS_AUTH
    # fail-closed：RBAC 配置不齐时一律视为未认证，不能放行
    if not (RBAC_API_URL and RBAC_CLIENT_ID and RBAC_CLIENT_SECRET):
        raise HTTPException(status_code=401, detail="RBAC not configured")
    jwt_token = extract_user_jwt(request)
    auth = await get_auth_info(jwt_token)
    if not auth:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return auth


def require_permission(permission: str):
    async def _check(auth: AuthInfo = Depends(require_auth)) -> AuthInfo:
        if not has_permission(auth.permissions, permission):
            raise HTTPException(
                status_code=403, detail=f"Forbidden: requires {permission}"
            )
        return auth

    return _check


def require_role(role_name: str):
    async def _check(auth: AuthInfo = Depends(require_auth)) -> AuthInfo:
        if not has_role(auth, role_name):
            raise HTTPException(
                status_code=403, detail=f"Forbidden: requires role {role_name}"
            )
        return auth

    return _check
