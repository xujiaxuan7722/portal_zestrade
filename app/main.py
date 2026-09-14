"""
main.py — ZesTrade 企业门户后端

路由总览（/api/* 未声明依赖的路由不存在——默认拒绝由"每条路由都显式挂依赖"保证）：
  GET  /api/me                      当前用户信息（登录即可）
  GET  /api/modules                 当前用户可见的模块列表（按权限码过滤）
  GET  /api/favorites               我的常用应用 id 列表（登录即可）
  PUT  /api/favorites               保存我的常用应用（整表替换，顺序即列表顺序）
  GET  /api/announcements           当前生效的横幅公告（按权限码过滤，规则同应用）
  GET/POST/PUT/DEL /api/admin/announcements[/{id}]  公告管理
  GET  /api/admin/audit             管理操作审计（分页：before_id 游标；筛选：actor/action/target_type/since/until）
  GET  /api/admin/modules           全部模块
  POST /api/admin/modules           新增模块
  PUT  /api/admin/modules/reorder   保存排序
  PUT  /api/admin/modules/{id}      修改模块
  DEL  /api/admin/modules/{id}      删除模块
  GET/PUT /api/admin/category-order 门户分类块自定义顺序（空 = 纯拼音；/api/modules 一并返回给门户）
  GET/PUT /api/admin/category-colors 分类底色（同分类同色，门户磁贴与后台徽标共用；/api/modules 一并返回）
  GET  /api/admin/permissions       RBAC 权限目录（"可见权限"选择器数据源）
  GET/POST /api/admin/custom-requires        门户自定义准入规则（跨应用/公告复用的手填条目）
  DELETE   /api/admin/custom-requires/{code} 删除并从所有应用/公告 requires 同步移除
  /api/admin/* 门禁统一为 require_permission(PORTAL_MANAGE_PERMISSION)，
  持 "*"（现 admin）或 portal:* 通配的用户天然通过
  GET  /login                       登录入口：跳共享 oauth2-proxy 的 /oauth2/start?rd=<回跳>
  GET  /logout                      登出：清缓存 + 跳 IdP 登出地址（bypass 演示模式回首页并提示）
  GET  /admin                       管理后台页面
  GET  /                            门户首页（静态）
"""

import hashlib
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import db, rbac_catalog
from . import auth as _auth
from .auth import (
    AuthInfo,
    build_login_url,
    build_logout_url,
    clear_auth_cache,
    close_http_client,
    get_auth_info,
    public_url,
    require_auth,
    require_permission,
    resolve_user_jwt,
    sanitize_return_to,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# 门户后台管理门禁的权限码。RBAC 登记前依赖 has_permission 的通配即可生效：
# 现 admin 角色持 "*" 天然通过；将来把此码挂给其他角色即可单独授权门户管理。
# 前端同款常量在 frontend/common.js，改名需同步。
PORTAL_MANAGE_PERMISSION = "portal:manage:console"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield
    await close_http_client()
    db.close_pool()


app = FastAPI(title="ZesTrade Portal", docs_url=None, redoc_url=None, lifespan=lifespan)


class ModuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=200)
    # 内置图标名（如 "cart"，见 frontend/icons.js）/ 图片 URL / 旧数据 emoji；
    # 空 = 前端显示模块名首字
    icon: str = Field(default="", max_length=200)
    url: str = Field(min_length=1, max_length=500)
    category: str = Field(default="", max_length=50)  # 空 = 门户归入"未分类"
    # 可见权限码（RBAC 唯一事实源，不建授权表）。必填、默认 []、不许 null——
    # 区分"故意公开"与"忘了填"由前端强制二选一保证。空列表 = 所有登录用户可见；
    # 元素为完整权限码 / 裸系统前缀 / "<system>:*" 系统通配（* 仅允许这一种写法）
    requires: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=50
    )
    enabled: bool = True
    status: Literal["normal", "maintenance"] = "normal"  # maintenance = 门户显示"维护中"
    owner_name: str = Field(default="", max_length=50)   # 系统负责人，"出问题找谁"

    @field_validator("url")
    @classmethod
    def _url_must_be_http(cls, v: str) -> str:
        # 门户卡片是 <a href> 直接跳转，禁掉 javascript: 等伪协议
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return v

    @field_validator("requires")
    @classmethod
    def _requires_wildcard_form(cls, v: list[str]) -> list[str]:
        return _validate_requires_wildcards(v)


def _validate_requires_wildcards(v: list[str]) -> list[str]:
    # 通配只有第一段能带 *，即 "<system>:*" 一种写法
    for code in v:
        if "*" in code and not re.fullmatch(r"[^:*]+:\*", code):
            raise ValueError(f"invalid wildcard in requires: {code!r}（仅支持 <system>:*）")
    return v


class AnnouncementIn(BaseModel):
    content: str = Field(min_length=1, max_length=200)
    level: Literal["info", "warning"] = "info"
    starts_at: Optional[datetime] = None  # None = 立即生效
    ends_at: Optional[datetime] = None    # None = 长期有效
    # 可见性与应用同一套权限码规则（见 ModuleIn.requires）
    requires: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=50
    )
    enabled: bool = True

    @field_validator("requires")
    @classmethod
    def _requires_wildcard_form(cls, v: list[str]) -> list[str]:
        return _validate_requires_wildcards(v)


class CustomRequireIn(BaseModel):
    """门户自定义准入规则（跨应用/公告复用的手填 requires 条目）。"""
    code: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=100)

    @field_validator("code")
    @classmethod
    def _code_form(cls, v: str) -> str:
        v = v.strip()
        if not v or any(ch.isspace() for ch in v):
            raise ValueError("权限码不能为空或含空白")
        return _validate_requires_wildcards([v])[0]


class ReorderIn(BaseModel):
    ids: list[int]


PALETTE_KEYS = db.PALETTE_KEYS   # 图标底色调色板（与 frontend/common.js 的 PALETTE 键一致）


class CategoryColorsIn(BaseModel):
    """分类 → 色名。键去空白、丢空串；值必须是调色板里的色名。"""
    colors: dict[str, str] = Field(default_factory=dict)

    @field_validator("colors")
    @classmethod
    def _clean(cls, v: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for k, c in v.items():
            k = k.strip()
            if not k:
                continue
            if len(k) > 50:
                raise ValueError("分类名不能超过 50 字")
            if c not in PALETTE_KEYS:
                raise ValueError(f"未知颜色 {c!r}，可选：{', '.join(PALETTE_KEYS)}")
            out[k] = c
        if len(out) > 100:
            raise ValueError("分类数量超限")
        return out


class CategoryOrderIn(BaseModel):
    """门户分类块顺序：分类名列表（去空白、去重、丢弃空串——"未分类"不参与排序，永远最后）。"""
    order: list[Annotated[str, Field(max_length=50)]] = Field(default_factory=list, max_length=100)

    @field_validator("order")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for c in v:
            c = c.strip()
            if c and c != "未分类" and c not in seen:
                seen.add(c)
                out.append(c)
        return out


class FavoritesIn(BaseModel):
    ids: list[int] = Field(max_length=200)


def _user_key(auth: AuthInfo) -> str:
    """常用应用按用户存储的 key：优先 RBAC 用户 id，兜底 email。"""
    user = auth.user or {}
    uid = str(user.get("id") or user.get("email") or "").strip()
    if not uid:
        raise HTTPException(status_code=400, detail="Cannot identify user")
    return uid


def _actor(auth: AuthInfo) -> str:
    """审计日志里记录的操作人。"""
    user = auth.user or {}
    return str(user.get("display_name") or user.get("email") or user.get("id") or "")


# ============================================================
# 用户侧
# ============================================================


@app.get("/api/me")
async def me(auth: AuthInfo = Depends(require_auth)):
    role_names = [r.get("name") for r in auth.roles if r.get("name")]
    return {"user": auth.user, "roles": role_names, "permissions": auth.permissions}


def _perm_matches(perm: str, req: str) -> bool:
    """一个用户权限码是否命中一条 requires：完整码相等 / requires 是裸系统前缀 /
    任一侧为 "<system>:*" 系统通配（写法合法性由 ModuleIn 校验保证）。"""
    if perm == req:
        return True
    perm_sys = perm.split(":", 1)[0]
    req_sys = req.split(":", 1)[0]
    if perm_sys == req:                              # requires 写的是裸系统前缀
        return True
    if perm == f"{perm_sys}:*" and req_sys == perm_sys:   # 用户持系统通配
        return True
    if req == f"{req_sys}:*" and perm_sys == req_sys:     # requires 配了系统通配
        return True
    return False


def requires_visible(requires: list[str], permissions: list[str]) -> bool:
    """requires 空 = 所有登录用户可见（RBAC 大量角色权限数为 0，此路径必须支持）；
    持 "*" 可见全部；否则命中任一 requires 即可见。"""
    if not requires:
        return True
    if "*" in permissions:
        return True
    return any(_perm_matches(p, r) for p in permissions for r in requires)


@app.get("/api/modules")
async def my_modules(auth: AuthInfo = Depends(require_auth)):
    """真正的可见性过滤在这里（后端），前端只负责展示。按权限码过滤；
    admin 持 "*"（RBAC 目录里 admin 角色挂 platform 的 *），天然可见全部启用模块。"""
    visible = [
        m for m in db.list_modules()
        if m["enabled"] and requires_visible(m["requires"], auth.permissions)
    ]
    return {
        "modules": visible,
        "category_order": db.get_category_order(),
        "category_colors": db.get_category_colors(),
    }


@app.get("/api/favorites")
async def my_favorites(auth: AuthInfo = Depends(require_auth)):
    return {"ids": db.get_favorites(_user_key(auth))}


@app.put("/api/favorites")
async def save_favorites(body: FavoritesIn, auth: AuthInfo = Depends(require_auth)):
    """只收留存在的模块 id（去重、保序）；可见性无需校验——前端展示时
    会与 /api/modules（后端已按角色过滤）取交集，收藏 id 本身不泄露信息。"""
    existing = {m["id"] for m in db.list_modules()}
    seen: set[int] = set()
    ids = [i for i in body.ids if i in existing and not (i in seen or seen.add(i))]
    db.set_favorites(_user_key(auth), ids)
    return {"ids": ids}


@app.get("/api/announcements")
async def my_announcements(auth: AuthInfo = Depends(require_auth)):
    """当前生效的横幅公告；与应用同一套权限码可见性规则。"""
    active = [
        a for a in db.list_active_announcements()
        if requires_visible(a["requires"], auth.permissions)
    ]
    return {"announcements": active}


# ============================================================
# 管理侧（全部 require_permission(PORTAL_MANAGE_PERMISSION)）
# ============================================================


@app.get("/api/admin/modules")
async def admin_list_modules(auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))):
    return {"modules": db.list_modules()}


@app.post("/api/admin/modules")
async def admin_create_module(
    body: ModuleIn, auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))
):
    created = db.create_module(body.model_dump())
    db.log_action(_actor(auth), "create", "module", created["id"], {"name": created["name"]})
    return created


# 注意：reorder 必须注册在 /{module_id} 之前，否则 "reorder" 会被当作路径参数
@app.put("/api/admin/modules/reorder")
async def admin_reorder_modules(
    body: ReorderIn, auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))
):
    db.reorder_modules(body.ids)
    db.log_action(_actor(auth), "reorder", "module", detail={"ids": body.ids})
    return {"ok": True}


@app.put("/api/admin/modules/{module_id}")
async def admin_update_module(
    module_id: int, body: ModuleIn, auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))
):
    updated = db.update_module(module_id, body.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="Module not found")
    db.log_action(_actor(auth), "update", "module", module_id, {"name": updated["name"]})
    return updated


@app.delete("/api/admin/modules/{module_id}")
async def admin_delete_module(
    module_id: int, auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))
):
    if not db.delete_module(module_id):
        raise HTTPException(status_code=404, detail="Module not found")
    db.log_action(_actor(auth), "delete", "module", module_id)
    return {"ok": True}


@app.get("/api/admin/category-order")
async def admin_get_category_order(auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))):
    return {"order": db.get_category_order()}


@app.put("/api/admin/category-order")
async def admin_set_category_order(
    body: CategoryOrderIn, auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))
):
    """整表替换。列表里的分类按此顺序排在门户最前；未列出的分类按拼音接在其后。"""
    order = db.set_category_order(body.order)
    db.log_action(_actor(auth), "reorder", "category", detail={"order": order})
    return {"order": order}


@app.get("/api/admin/category-colors")
async def admin_get_category_colors(auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))):
    return {"colors": db.get_category_colors()}


@app.put("/api/admin/category-colors")
async def admin_set_category_colors(
    body: CategoryColorsIn, auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))
):
    """整表替换：只存显式选过色的分类；没存的分类前端按名字哈希取默认色。"""
    colors = db.set_category_colors(body.colors)
    db.log_action(_actor(auth), "update", "category", detail={"colors": colors})
    return {"colors": colors}


@app.get("/api/admin/permissions")
async def admin_permission_catalog(auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))):
    """RBAC 权限目录（服务端缓存 + ETag + 旧缓存兜底），模块"可见权限"选择器数据源。"""
    try:
        return await rbac_catalog.get_catalog()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ── 门户自定义准入规则 ──
# RBAC 仍是唯一事实源：这里只维护管理员手填的 requires 写法（裸前缀 / <system>:* /
# RBAC 尚未登记的完整码），供所有应用与公告的可见性选择器共用；不产生任何权限。


async def _catalog_codes() -> set[str] | None:
    """RBAC 目录里的完整码集合；目录不可用返回 None（不阻塞自定义规则的增删）。"""
    try:
        cat = await rbac_catalog.get_catalog()
    except RuntimeError:
        return None
    return {p.get("code") for p in (cat or {}).get("permissions", []) if p.get("code")}


@app.get("/api/admin/custom-requires")
async def admin_list_custom_requires(
    auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION)),
):
    """自定义规则列表。顺带对账：某条码已在 RBAC 目录登记，则自动归还目录
    （从自定义表删除，不动引用它的应用/公告——码本身仍有效）。"""
    items = db.list_custom_requires()
    known = await _catalog_codes()
    if known:
        merged = [c for c in items if c["code"] in known]
        for c in merged:
            db.retire_custom_require(c["code"])
        items = [c for c in items if c["code"] not in known]
    return {"items": items}


@app.post("/api/admin/custom-requires")
async def admin_add_custom_require(
    body: CustomRequireIn, auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))
):
    known = await _catalog_codes()
    if known and body.code in known:
        raise HTTPException(
            status_code=400, detail=f"{body.code} 已在 RBAC 权限目录中，直接勾选即可，无需自定义"
        )
    item = db.add_custom_require(body.code, body.note, _actor(auth))
    db.log_action(_actor(auth), "create", "custom_require", detail={"code": body.code})
    return item


@app.delete("/api/admin/custom-requires/{code:path}")
async def admin_delete_custom_require(
    code: str, auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))
):
    """删除规则，并从所有引用它的应用/公告 requires 中同步移除。"""
    removed = db.delete_custom_require(code)
    if removed is None:
        raise HTTPException(status_code=404, detail="Custom require not found")
    db.log_action(_actor(auth), "delete", "custom_require", detail={"code": code, "removed_from": removed})
    return {"ok": True, "removed_from": removed}


# ── 横幅公告管理 ──


@app.get("/api/admin/announcements")
async def admin_list_announcements(auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))):
    return {"announcements": db.list_announcements()}


@app.post("/api/admin/announcements")
async def admin_create_announcement(
    body: AnnouncementIn, auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))
):
    created = db.create_announcement(body.model_dump())
    db.log_action(_actor(auth), "create", "announcement", created["id"],
                  {"content": created["content"]})
    return created


@app.put("/api/admin/announcements/{ann_id}")
async def admin_update_announcement(
    ann_id: int, body: AnnouncementIn, auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))
):
    updated = db.update_announcement(ann_id, body.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="Announcement not found")
    db.log_action(_actor(auth), "update", "announcement", ann_id,
                  {"content": updated["content"]})
    return updated


@app.delete("/api/admin/announcements/{ann_id}")
async def admin_delete_announcement(
    ann_id: int, auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION))
):
    if not db.delete_announcement(ann_id):
        raise HTTPException(status_code=404, detail="Announcement not found")
    db.log_action(_actor(auth), "delete", "announcement", ann_id)
    return {"ok": True}


AUDIT_PAGE_MAX = 200


def _parse_day(value: Optional[str], name: str) -> Optional[datetime]:
    """审计筛选的日期参数：YYYY-MM-DD，解析失败 400。"""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} 须为 YYYY-MM-DD")


@app.get("/api/admin/audit")
async def admin_audit_logs(
    limit: int = 50,
    before_id: Optional[int] = None,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    target_type: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    auth: AuthInfo = Depends(require_permission(PORTAL_MANAGE_PERMISSION)),
):
    """审计分页：按 id 倒序，游标 before_id 取更早一页；返回 next_before_id 为 None 表示没有更多。
    until 为日期时含当天整天（右开区间取次日零点）。"""
    page = min(max(limit, 1), AUDIT_PAGE_MAX)
    until_dt = _parse_day(until, "until")
    if until_dt is not None:
        until_dt = until_dt.replace(hour=0, minute=0) + timedelta(days=1)
    logs = db.list_audit_logs(
        page, before_id=before_id, actor=(actor or "").strip() or None,
        action=action or None, target_type=target_type or None,
        since=_parse_day(since, "since"), until=until_dt,
    )
    return {"logs": logs, "next_before_id": logs[-1]["id"] if len(logs) == page else None}


# ============================================================
# 登出 + 页面
# ============================================================


@app.get("/login")
async def login(request: Request, returnTo: Optional[str] = None):
    """登录入口：送去共享 oauth2-proxy（sso）登录，登录后回到 returnTo（只接受站内地址）。
    演示模式没有登录这回事，直接回首页；未配置 OAUTH2_PROXY_URL 时也回首页（靠代理层拦截）。"""
    if _auth.AUTH_BYPASS:
        return RedirectResponse(url="/")
    return_to = sanitize_return_to(request, returnTo)
    if return_to and return_to.startswith("/"):
        return_to = public_url(request, return_to)
    url = build_login_url(request, return_to)
    return RedirectResponse(url=url or "/")


@app.get("/logout")
async def logout(request: Request, returnTo: Optional[str] = None):
    if _auth.AUTH_BYPASS:
        # 演示模式没有登录态可退：不跳 Keycloak（无 client_id 会被拒或退了个寂寞），
        # 回首页并让前端提示"演示模式退出不生效"。切换用户只能改 AUTH_BYPASS_* 重启。
        return RedirectResponse(url="/?demo_logout=1")
    jwt_token = await resolve_user_jwt(request)
    clear_auth_cache(jwt_token, request)
    # 防开放重定向：非站内地址的 returnTo 会被丢弃（登出仍生效）
    return_to = sanitize_return_to(request, returnTo)
    if return_to and return_to.startswith("/"):
        return_to = public_url(request, return_to)   # sso 的 rd 必须是完整公网地址
    return RedirectResponse(url=build_logout_url(request, return_to=return_to))


# 静态资源版本号：取 css/js 内容哈希，页面里的引用写成 /common.css?v=<hash>。
# 资源一变地址就变，浏览器不可能再用旧缓存（no-cache 头只管"下次取"，管不了已存的旧副本）。
_ASSET_FILES = ("common.css", "common.js", "icons.js")


def _asset_version() -> str:
    h = hashlib.sha1()
    for name in _ASSET_FILES:
        h.update((FRONTEND_DIR / name).read_bytes())
    return h.hexdigest()[:10]


ASSET_VERSION = _asset_version()


def _page(name: str) -> HTMLResponse:
    html = (FRONTEND_DIR / name).read_text(encoding="utf-8")
    for asset in _ASSET_FILES:
        html = html.replace(f'"/{asset}"', f'"/{asset}?v={ASSET_VERSION}"')
    return HTMLResponse(html)


async def _page_or_login(request: Request, name: str):
    """页面路由：未登录且配置了共享 oauth2-proxy 时，直接把浏览器送去 sso 登录，
    登录后回到当前页；否则照常出页面（API 仍逐条 401，页面里显示"重新登录"）。"""
    if not _auth.AUTH_BYPASS and _auth.OAUTH2_PROXY_URL:
        if not await get_auth_info(await resolve_user_jwt(request)):
            return RedirectResponse(url=build_login_url(request, public_url(request)))
    return _page(name)


@app.get("/")
async def index_page(request: Request):
    return await _page_or_login(request, "index.html")


@app.get("/admin")
async def admin_page(request: Request):
    return await _page_or_login(request, "admin.html")


@app.middleware("http")
async def _static_no_cache(request: Request, call_next):
    """前端静态文件（html/css/js）每次回源校验：StaticFiles 只带 ETag/Last-Modified，
    不带 Cache-Control 时浏览器会按启发式缓存 css/js 数天，发版后就出现"HTML 是新的、
    样式是旧的"错位。no-cache 让浏览器每次用 ETag 问一次（304 极小），不是不缓存。"""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".css", ".js")) or path == "/admin":
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


# 静态资源必须最后 mount，否则会盖住上面的 API 路由
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
