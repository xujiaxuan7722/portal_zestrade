"""
main.py — ZesTrade 企业门户后端

路由总览（/api/* 未声明依赖的路由不存在——默认拒绝由"每条路由都显式挂依赖"保证）：
  GET  /api/me                      当前用户信息（登录即可）
  GET  /api/modules                 当前用户可见的模块列表（按角色过滤）
  GET  /api/admin/modules           全部模块（admin）
  POST /api/admin/modules           新增模块（admin）
  PUT  /api/admin/modules/reorder   保存排序（admin）
  PUT  /api/admin/modules/{id}      修改模块（admin）
  DEL  /api/admin/modules/{id}      删除模块（admin）
  GET  /api/admin/roles             已知角色列表（admin）
  POST /api/admin/roles             手动添加角色名（admin）
  DEL  /api/admin/roles/{name}      移除角色名（admin）
  GET  /logout                      登出：清缓存 + 跳 IdP 登出地址
  GET  /admin                       管理后台页面
  GET  /                            门户首页（静态）
"""

from pathlib import Path
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .auth import (
    AuthInfo,
    build_logout_url,
    clear_auth_cache,
    extract_user_jwt,
    require_auth,
    require_role,
    sanitize_return_to,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="ZesTrade Portal", docs_url=None, redoc_url=None)
db.init_db()


class ModuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=200)
    icon: str = Field(default="📦", max_length=200)
    url: str = Field(min_length=1, max_length=500)
    category: str = Field(default="", max_length=50)  # 空 = 门户侧边栏归入"未分类"
    # 空列表 = 所有人可见；单个角色名限 1-50 字符（与 RoleIn 一致），最多 50 个
    visible_roles: list[Annotated[str, Field(min_length=1, max_length=50)]] = Field(
        default_factory=list, max_length=50
    )
    enabled: bool = True


class ReorderIn(BaseModel):
    ids: list[int]


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)


# ============================================================
# 用户侧
# ============================================================


@app.get("/api/me")
async def me(auth: AuthInfo = Depends(require_auth)):
    role_names = [r.get("name") for r in auth.roles if r.get("name")]
    # 顺手把该用户的角色收录进已知角色池（管理后台下拉框数据源）
    db.record_roles(role_names)
    return {"user": auth.user, "roles": role_names, "permissions": auth.permissions}


@app.get("/api/modules")
async def my_modules(auth: AuthInfo = Depends(require_auth)):
    """真正的可见性过滤在这里（后端），前端只负责展示。"""
    my_roles = {r.get("name") for r in auth.roles}
    visible = [m for m in db.list_modules() if m["enabled"]]
    # admin 在门户首页可见全部启用模块（便于总览自检）；其他角色按 visible_roles 过滤
    if "admin" not in my_roles:
        visible = [
            m
            for m in visible
            if not m["visible_roles"] or my_roles & set(m["visible_roles"])
        ]
    return {"modules": visible}


# ============================================================
# 管理侧（全部 require_role("admin")）
# ============================================================


@app.get("/api/admin/modules")
async def admin_list_modules(auth: AuthInfo = Depends(require_role("admin"))):
    return {"modules": db.list_modules()}


@app.post("/api/admin/modules")
async def admin_create_module(
    body: ModuleIn, auth: AuthInfo = Depends(require_role("admin"))
):
    db.record_roles(body.visible_roles, source="manual")
    return db.create_module(body.model_dump())


# 注意：reorder 必须注册在 /{module_id} 之前，否则 "reorder" 会被当作路径参数
@app.put("/api/admin/modules/reorder")
async def admin_reorder_modules(
    body: ReorderIn, auth: AuthInfo = Depends(require_role("admin"))
):
    db.reorder_modules(body.ids)
    return {"ok": True}


@app.put("/api/admin/modules/{module_id}")
async def admin_update_module(
    module_id: int, body: ModuleIn, auth: AuthInfo = Depends(require_role("admin"))
):
    db.record_roles(body.visible_roles, source="manual")
    updated = db.update_module(module_id, body.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="Module not found")
    return updated


@app.delete("/api/admin/modules/{module_id}")
async def admin_delete_module(
    module_id: int, auth: AuthInfo = Depends(require_role("admin"))
):
    if not db.delete_module(module_id):
        raise HTTPException(status_code=404, detail="Module not found")
    return {"ok": True}


@app.get("/api/admin/roles")
async def admin_list_roles(auth: AuthInfo = Depends(require_role("admin"))):
    return {"roles": db.list_known_roles()}


@app.post("/api/admin/roles")
async def admin_add_role(
    body: RoleIn, auth: AuthInfo = Depends(require_role("admin"))
):
    db.record_roles([body.name], source="manual")
    return {"roles": db.list_known_roles()}


@app.delete("/api/admin/roles/{name}")
async def admin_delete_role(
    name: str, force: bool = False, auth: AuthInfo = Depends(require_role("admin"))
):
    """删角色联动：仍被模块引用时返回 409 + 引用清单；force=true 时先从
    这些模块的 visible_roles 中移除再删（避免模块变成"谁也看不见"）。"""
    using = db.modules_using_role(name)
    if using and not force:
        raise HTTPException(
            status_code=409,
            detail={"message": "role in use", "modules": [m["name"] for m in using]},
        )
    if using:
        db.remove_role_from_modules(name)
    if not db.delete_known_role(name):
        raise HTTPException(status_code=404, detail="Role not found")
    return {"roles": db.list_known_roles()}


# ============================================================
# 登出 + 页面
# ============================================================


@app.get("/logout")
async def logout(request: Request, returnTo: Optional[str] = None):
    jwt_token = extract_user_jwt(request)
    clear_auth_cache(jwt_token)
    # 防开放重定向：非站内地址的 returnTo 会被丢弃（登出仍生效）
    return_to = sanitize_return_to(request, returnTo)
    return RedirectResponse(url=build_logout_url(request, return_to=return_to))


@app.get("/admin")
async def admin_page():
    return FileResponse(FRONTEND_DIR / "admin.html")


# 静态资源必须最后 mount，否则会盖住上面的 API 路由
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
