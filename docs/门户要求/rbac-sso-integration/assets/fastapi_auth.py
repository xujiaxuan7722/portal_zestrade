"""
fastapi_auth.py — FastAPI (Python) 接入 RBAC 权限系统（单文件，复制即用）

此文件对所有下游应用通用，不论你的 IdP 是 CF Access 还是 Keycloak —— 只是登出 URL 不同。

============================================================
本文件提供 3 个核心能力
============================================================

  1) 登录后用 JWT 换权限
        get_auth_info(jwt) → AuthInfo（内部缓存 5 分钟）

  2) 根据缓存权限做权限判断
        has_permission(permissions, "read:orders")
        has_role(auth, "admin")

  3) 登出清理
        clear_auth_cache(jwt) + RedirectResponse(build_logout_url(request, ...))
        登出 URL 由 IDP/KEYCLOAK_LOGOUT_URL 决定，支持 returnTo 透传。
        CF Access 直接用本应用 origin (`<host>/cdn-cgi/access/logout`)。

============================================================
JWT 提取来源 (extract_user_jwt)
============================================================

CF Access 应用:
  ① 优先 Cf-Access-Jwt-Assertion 头 (CF 边缘自动注入)
  ② 回落到 CF_Authorization cookie (本地 dev / 边缘绕过场景)

Keycloak 应用:
  ① Authorization: Bearer <token>  (oauth2-proxy 默认)
  ② X-Auth-Request-Access-Token   (oauth2-proxy 备选)
  ③ 自己的 session 层 / 自实现 OAuth2 客户端: 用 framework 提供的 helper 取

样本里默认启用 CF 那组, Keycloak 那组用注释保留供切换。
============================================================

FastAPI 依赖注入封装（更方便）:
  Depends(require_auth)                  — 要求已登录，返回 AuthInfo
  Depends(require_permission("read:x"))  — 要求拥有指定权限
  Depends(require_role("admin"))         — 要求拥有指定角色

============================================================
统一接入端点 — 不分 IdP
============================================================

  POST https://rbac.bogoo.ai/api/service/users/by-jwt
  Headers:
    CF-Access-Client-Id:     <Service Token Client ID>
    CF-Access-Client-Secret: <Service Token Client Secret>
    Content-Type:            application/json
  Body:
    { "jwt": "<用户原始 JWT, 不要加 Bearer 前缀>" }

  RBAC 内部按 JWT 的 iss 自动路由到 CF Access / Keycloak 对应的 JWKS 验签。
  你的应用拿到啥 JWT 就直接转发, 不需要关心 IdP。

============================================================
环境变量
============================================================

  RBAC_API_URL=https://rbac.bogoo.ai
  RBAC_CLIENT_ID=<service-token-client-id>.access
  RBAC_CLIENT_SECRET=<service-token-client-secret>

  # 登出配置(默认 keycloak,因为大多数用户面向应用走 Keycloak;CF Access 主要是 API/server-to-server)
  IDP=keycloak                    # 或 cf-access
  KEYCLOAK_LOGOUT_URL=https://auth.duoweitree.com/realms/sso/protocol/openid-connect/logout
                                   # 仅 IDP=keycloak 时使用

  # IDP=cf-access 不需要任何额外环境变量 —— CF Access 的登出 URL 直接构造为
  # `<本应用 origin>/cdn-cgi/access/logout`,CF Access 在边缘拦截该路径并清 cookie。
  # 不要用 team-domain (https://<team>.cloudflareaccess.com/cdn-cgi/access/logout) ——
  # 那个 URL 需要 team-domain 自己的 cookie,在普通应用 session 中并不存在。

============================================================
配置验证（手工自检 URL）
============================================================
设置好环境变量后,把 build_logout_url() 的输出直接贴到浏览器:

  CF Access 应用: 浏览器访问 https://<your-app>/cdn-cgi/access/logout
                 应看到 CF Access "You are logged out" 页或 returnTo 跳转。

  Keycloak 应用: 浏览器访问 <KEYCLOAK_LOGOUT_URL>
                 应看到 Keycloak 登出确认页 (如带 id_token_hint 则跳过确认)。

⚠ Keycloak 注意事项:
  - 如果你想登出后跳回应用 (?post_logout_redirect_uri=<URL>),该 URL 必须先在
    Keycloak Admin Console → 你的 client → Settings → "Valid post logout
    redirect URIs" 里加白名单, 否则 Keycloak 会拒绝重定向。
  - 不带 id_token_hint 时 Keycloak 会显示"确认登出"中间页, 用户体验差。
    id_token_hint 应来自登录时拿到的 id_token, 由你的 session 层存储后取出。
============================================================

============================================================
接入前必备清单
============================================================

  [ ] 1. 你的站点已通过某个 IdP（CF Access 或 Keycloak）保护
  [ ] 2. 你已在 CF Zero Trust 申请到一个 RBAC 用的 Service Token
        （Client ID + Client Secret），并已在 rbac.bogoo.ai Application
        的 Policy 中放行该 Service Token
  [ ] 3. 管理员已在 RBAC 后台为你的业务创建并分配了权限码

============================================================
接入步骤
============================================================

  Step 1 — 复制本文件到你的项目（如 app/auth.py）

  Step 2 — 安装依赖:
           pip install fastapi uvicorn httpx

  Step 3 — 设置环境变量（参考上方"环境变量"小节）

  Step 4 — 在路由中使用依赖注入:
           @app.get("/api/orders")
           async def list_orders(auth: AuthInfo = Depends(require_permission("read:orders"))):
               return {"user": auth.user}

  Step 5 — 添加 /logout 路由（见底部示例; 支持 ?returnTo=<url> 透传）

============================================================
缓存说明
============================================================

  缓存位置: 进程内存 dict（适合单实例，重启后清空）
  缓存 Key:  SHA256(JWT).hex[:32]
  缓存 TTL:  5 分钟
  权限变更生效: 退出重新登录 = 立即; 不退出 = 最多 5 分钟
  多 worker 注意: gunicorn 多进程每个独立缓存，可改用 Redis
"""

import os
import re
import time
import hashlib
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

# ============================================================
# 配置
# ============================================================

RBAC_API_URL = os.getenv("RBAC_API_URL", "https://rbac.bogoo.ai")
RBAC_CLIENT_ID = os.getenv("RBAC_CLIENT_ID", "")
RBAC_CLIENT_SECRET = os.getenv("RBAC_CLIENT_SECRET", "")
CACHE_TTL = 300  # 5 分钟

# 登出 URL 配置：默认 keycloak（大多数用户面向应用）
# CF Access 不需要任何 logout 相关 env —— 用本应用 origin
IDP = os.getenv("IDP", "keycloak").strip().lower()  # "cf-access" 或 "keycloak"
KEYCLOAK_LOGOUT_URL = os.getenv(
    "KEYCLOAK_LOGOUT_URL",
    "https://auth.duoweitree.com/realms/sso/protocol/openid-connect/logout",
)


def _validate_idp_config() -> None:
    """模块加载即校验配置；不一致直接 fail loudly。"""
    if IDP not in ("cf-access", "keycloak"):
        raise RuntimeError(
            f"IDP env must be 'cf-access' or 'keycloak', got: {IDP!r}"
        )
    # IDP=cf-access: 登出 URL 用本应用 origin, 不需要任何额外 env
    if IDP == "keycloak" and not KEYCLOAK_LOGOUT_URL:
        raise RuntimeError("IDP=keycloak requires KEYCLOAK_LOGOUT_URL env")


_validate_idp_config()


# ============================================================
# 类型定义 — 对齐 /api/service/users/by-jwt 的响应结构
# ============================================================


@dataclass
class AuthInfo:
    matched: bool
    matched_by: Optional[str]  # "unionid" | "userid" | "mobile" | "email"
    #                          # | "provider_sub" | "created" | None
    user: Optional[dict]  # {id, display_name, email, mobile, status} 或 None
    roles: list[dict]  # [{id, name, source}, ...]
    permissions: list[str]  # ["admin:*", "read:users", ...]
    contacts: dict  # {dingtalk_unionid?, dingtalk_userid?, identity_emails?}


@dataclass
class _CacheEntry:
    data: AuthInfo
    expires_at: float


# ============================================================
# 提取用户 JWT — 应用按自己的 IdP 选择一个
# ============================================================


def extract_user_jwt(request: Request) -> str:
    """
    从请求中提取用户的原始 JWT, 转发给 RBAC.

    根据你的 IdP 选择 "ONE OF" 下面一组实现 — 保留一组, 注释掉另一组.

    CF Access:  Cf-Access-Jwt-Assertion 头 → CF_Authorization cookie 兜底
    Keycloak:   Authorization: Bearer → X-Auth-Request-Access-Token → 自己的 session
    """
    # ──── For CF Access apps ────
    # CF Access edge 会注入 Cf-Access-Jwt-Assertion 头, cookie 作为兜底
    # (本地 dev 或边缘没注入头时使用)。
    header = request.headers.get("Cf-Access-Jwt-Assertion") or ""
    if header:
        return header
    cookie_header = request.headers.get("Cookie") or ""
    m = re.search(r'CF_Authorization=([^\s;]+)', cookie_header)
    return m.group(1) if m else ""

    # ──── For Keycloak apps ────
    # 注释掉上面的 CF 块, 启用下面这组:
    # 最常见: oauth2-proxy + --pass-access-token=true → Authorization 头
    # 备选:   oauth2-proxy 的 X-Auth-Request-Access-Token 头
    # 自实现 OAuth2 客户端: 从你的 session 层取 access_token
    #
    # auth = request.headers.get("Authorization") or ""
    # if auth.lower().startswith("bearer "):
    #     return auth[7:].strip()
    # return request.headers.get("X-Auth-Request-Access-Token") or ""


# ============================================================
# 内存缓存
# ============================================================

_auth_cache: dict[str, _CacheEntry] = {}


def _cache_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def _get_cached(token: str) -> Optional[AuthInfo]:
    entry = _auth_cache.get(_cache_key(token))
    if entry and entry.expires_at > time.time():
        return entry.data
    if entry:
        _auth_cache.pop(_cache_key(token), None)
    return None


def _set_cached(token: str, data: AuthInfo) -> None:
    _auth_cache[_cache_key(token)] = _CacheEntry(
        data=data, expires_at=time.time() + CACHE_TTL
    )


def _clear_cached(token: str) -> None:
    _auth_cache.pop(_cache_key(token), None)


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


async def get_auth_info(jwt_token: str) -> Optional[AuthInfo]:
    """
    用 JWT 换取权限信息。

    - 首次：调 RBAC `/api/service/users/by-jwt`，结果缓存 5 分钟
    - 后续：从内存读
    - matched=False → 返回 None（视为未认证）
    """
    if not jwt_token:
        return None

    cached = _get_cached(jwt_token)
    if cached:
        return cached if cached.matched else None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{RBAC_API_URL}/api/service/users/by-jwt",
                headers={
                    "CF-Access-Client-Id": RBAC_CLIENT_ID,
                    "CF-Access-Client-Secret": RBAC_CLIENT_SECRET,
                    "Content-Type": "application/json",
                },
                json={"jwt": jwt_token},
            )
        if resp.status_code != 200:
            return None
        auth = _to_auth_info(resp.json())
    except Exception:
        return None

    _set_cached(jwt_token, auth)
    return auth if auth.matched else None


def clear_auth_cache(jwt_token: str) -> None:
    """登出时调用：清掉本地缓存。"""
    if jwt_token:
        _clear_cached(jwt_token)


# ============================================================
# 核心：权限/角色判断
# ============================================================


def has_permission(permissions: list[str], required: str) -> bool:
    """
    权限格式: "action:resource"，支持通配符：
      - "*"        匹配所有
      - "admin:*"  匹配 admin:xxx
      - "read:users" 精确匹配
    """
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


def build_logout_url(
    request: Request,
    return_to: Optional[str] = None,
    id_token_hint: Optional[str] = None,
) -> str:
    """
    构造 IdP 登出 URL。

    - CF Access  → <本应用 origin>/cdn-cgi/access/logout?returnTo=<url>
                   CF Access 在边缘拦截该路径并清 cookie。一定用应用自己的 origin,
                   不要用 team-domain (那个 URL 需要 team-domain cookie, 应用 session
                   里没有, 会报"No Access cookie found")。

    - Keycloak   → <KEYCLOAK_LOGOUT_URL>?post_logout_redirect_uri=<url>&id_token_hint=<jwt>
                   ⚠ post_logout_redirect_uri 必须先在 Keycloak Admin Console 里
                     Client → Settings → "Valid post logout redirect URIs" 加白名单。
    """
    params: dict[str, str] = {}
    if IDP == "cf-access":
        if return_to:
            params["returnTo"] = return_to
        # 用本应用 origin (scheme://host) — CF Access 会在边缘清 cookie
        base = f"{request.url.scheme}://{request.url.netloc}/cdn-cgi/access/logout"
    else:  # keycloak
        if return_to:
            params["post_logout_redirect_uri"] = return_to
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        base = KEYCLOAK_LOGOUT_URL
    if params:
        return f"{base}?{urlencode(params)}"
    return base


# ============================================================
# FastAPI 依赖注入
# ============================================================


async def require_auth(request: Request) -> AuthInfo:
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


# ============================================================
# 使用示例：完整 FastAPI 应用
# ============================================================

# app = FastAPI(title="My App")


# @app.get("/logout")
# async def logout(request: Request, returnTo: Optional[str] = None):
#     """登出：清缓存 + 跳 IdP 登出。
#
#     支持 ?returnTo=<url> 透传:
#       - CF Access:  <app-origin>/cdn-cgi/access/logout?returnTo=<url>
#       - Keycloak:   <KEYCLOAK_LOGOUT_URL>?post_logout_redirect_uri=<url>
#     """
#     jwt_token = extract_user_jwt(request)
#     clear_auth_cache(jwt_token)
#
#     # If your app stored the id_token from login (via your session layer),
#     # pass it as id_token_hint for cleaner Keycloak logout (skips the confirmation page):
#     # id_token = request.session.get("id_token")
#     # url = build_logout_url(request, return_to=returnTo, id_token_hint=id_token)
#     url = build_logout_url(request, return_to=returnTo)
#     return RedirectResponse(url=url)


# @app.get("/api/me")
# async def me(auth: AuthInfo = Depends(require_auth)):
#     return {
#         "user": auth.user,
#         "roles": [r["name"] for r in auth.roles],
#         "permissions": auth.permissions,
#     }


# @app.get("/api/users")
# async def list_users(auth: AuthInfo = Depends(require_permission("read:users"))):
#     return {"message": f"Hello {auth.user['display_name']}, you can read users"}


# @app.get("/admin/dashboard")
# async def admin_dashboard(auth: AuthInfo = Depends(require_role("write:admin"))):
#     return {"message": f"Welcome admin {auth.user['display_name']}"}


# 启动: uvicorn fastapi_auth:app --host 0.0.0.0 --port 8000
