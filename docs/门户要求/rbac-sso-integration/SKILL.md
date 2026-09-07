---
name: rbac-sso-integration
description: >
  公司内部系统接入统一 RBAC 权限服务（rbac.bogoo.ai）与 SSO（Cloudflare Access /
  Keycloak+oauth2-proxy）的标准协议与参考实现。用于任何新系统/新服务需要"登录 + 权限校验 +
  登出"时，不论后端语言是否为 Python/FastAPI。提供框架无关的接入协议说明、环境变量清单、
  常见坑，以及可直接复制的 FastAPI 单文件模板（assets/fastapi_auth.py）。来源于 pm_system
  项目的生产实现（backend/shared/auth.py），已验证可用。
---

# RBAC / SSO 接入规范

## 背景

公司内部系统的登录由边缘 IdP（Cloudflare Access 或 Keycloak，经 oauth2-proxy）完成，
**业务系统本身不做登录页 / 密码校验**，只需要：

1. 从请求中提取 IdP 签发的用户 JWT
2. 用该 JWT 向统一 RBAC 服务 `rbac.bogoo.ai` 换取角色 + 权限
3. 在路由层用权限码做 401/403 校验
4. 登出时清本地缓存 + 跳转到对应 IdP 的登出地址

这个协议与后端语言无关。FastAPI 有现成的可复制模板（`assets/fastapi_auth.py`），
其他语言/框架按下面的协议照做即可。

## 接入前置清单

- [ ] 应用已被 CF Access 或 Keycloak（经 oauth2-proxy）保护
- [ ] 已在 CF Zero Trust 申请到 RBAC 用的 Service Token（Client ID + Secret），
      并已在 rbac.bogoo.ai 对应 Application 的 Policy 中放行该 Service Token
- [ ] RBAC 管理员已为业务创建并分配好权限码
- [ ] 前端顶栏已实现用户头像 + 下拉菜单 + 退出登录（见「前端集成模式」第 2 点），
      不是可选项

## 核心协议（框架无关）

### 1. 提取用户 JWT

请求头按以下优先级尝试（同时兼容 keycloak 与 cf-access 两条链路，取第一个非空的）：

1. `X-Auth-Request-Access-Token`（keycloak，oauth2-proxy 透传）
2. `X-Forwarded-Access-Token`（keycloak，oauth2-proxy 兜底）
3. `Authorization: Bearer <token>`（keycloak）
4. `Cf-Access-Jwt-Assertion`（cloudflare access，边缘自动注入）
5. `CF_Authorization` cookie（cloudflare access 兜底，本地开发 / 边缘绕过场景）

单一 IdP 的应用只需实现自己那一条链路（见 `assets/fastapi_auth.py` 里的
`extract_user_jwt`，CF/Keycloak 两组实现二选一并保留注释）。

### 2. 用 JWT 换权限

```
POST {RBAC_API_URL}/api/service/users/by-jwt
Headers:
  CF-Access-Client-Id:     <Service Token Client ID>
  CF-Access-Client-Secret: <Service Token Client Secret>
  Content-Type:            application/json
Body:
  { "jwt": "<用户原始 JWT，不要加 Bearer 前缀>" }
```

返回体：

```json
{
  "matched": true,
  "matched_by": "unionid|userid|mobile|email|provider_sub|created",
  "user": {"id": "...", "display_name": "...", "email": "...", "mobile": "...", "status": "..."},
  "roles": [{"id": "...", "name": "admin", "source": "..."}],
  "permissions": ["read:orders", "write:*", "admin:*"],
  "contacts": {}
}
```

RBAC 会按 JWT 的 `iss` 自动路由到对应 IdP 的 JWKS 验签，业务方不需要自己验签，
也不需要关心用户当前走的是 CF Access 还是 Keycloak。

结果建议内存缓存 5 分钟（key = `sha256(jwt)[:32]`），避免每请求都打 RBAC。
多进程 / 多实例部署下各自独立缓存；如需权限变更秒级生效，可换 Redis 共享缓存
（一般不必要——退出重新登录即时生效，不退出最多延迟 5 分钟是预期行为）。

### 3. 权限判断

权限码格式 `{action}:{resource}`，如 `read:orders`、`write:*`、`*`（全通配）。

```
has_permission(perms, "read:orders"):
  "*" in perms            → True
  "read:orders" in perms  → True
  f"{action}:*" in perms  → True   # action = "read:orders".split(":")[0] = "read"
```

角色判断：`any(r.name == role_name for r in roles)`。

### 4. 登出

```
IDP=cf-access → 跳 <本应用 origin>/cdn-cgi/access/logout?returnTo=<url>
                必须用本应用 origin，不能用 team-domain 版本
IDP=keycloak  → 跳 KEYCLOAK_LOGOUT_URL?post_logout_redirect_uri=<url>&id_token_hint=<jwt>
                post_logout_redirect_uri 需先在 Keycloak Client 白名单里加
```

登出时先清本地缓存（`clear_auth_cache(jwt)`），再跳转。若走 oauth2-proxy 且配置了
`OAUTH2_PROXY_SIGN_OUT_URL`，直接跳该地址（由 oauth2-proxy 自己清会话并调用其配置的
Keycloak 登出地址），`?returnTo=` 透传为 `?rd=`。

### 5. 环境变量

```env
RBAC_API_URL=https://rbac.bogoo.ai
RBAC_CLIENT_ID=<service-token-client-id>.access
RBAC_CLIENT_SECRET=<service-token-client-secret>

IDP=keycloak                      # 或 cf-access；cf-access 不需要下面任何登出相关变量
KEYCLOAK_LOGOUT_URL=https://auth.duoweitree.com/realms/sso/protocol/openid-connect/logout
OAUTH2_PROXY_SIGN_OUT_URL=https://<oauth2-proxy-host>/oauth2/sign_out   # 可选

AUTH_BYPASS=false                 # 开发环境可设 true，绕过认证并返回全权限测试用户
```

未配置 `RBAC_API_URL` / `RBAC_CLIENT_ID` / `RBAC_CLIENT_SECRET`（且 `AUTH_BYPASS`
未开启）时应直接判定为未认证，不能放行。

## FastAPI 参考实现

`assets/fastapi_auth.py` 是可直接复制的单文件实现，来自 pm_system 项目
（对应 `backend/shared/auth.py`）并经生产验证。提供 3 个依赖注入：

```python
Depends(require_auth)                  # 要求已登录，返回 AuthInfo
Depends(require_permission("read:x"))  # 要求拥有指定权限
Depends(require_role("admin"))         # 要求拥有指定角色
```

接入步骤：复制到新项目（如 `app/auth.py`）→ `pip install fastapi uvicorn httpx`
→ 设环境变量 → 路由里用 `Depends(...)` → 加一个 `/logout` 路由（文件底部有示例）。
按项目的 IdP，在 `extract_user_jwt` 里保留 CF 或 Keycloak 那一组实现即可。

### 路由多时的推荐模式：规则表而非逐条声明

接口数量多的系统，不建议每条路由手写 `Depends(require_permission(...))`，而是用一张
「方法 + 路径模式 → 权限要求」规则表，在全局依赖里统一判断。参考 pm_system
`backend/shared/auth.py` 里的 `_ROUTE_RULES` / `resolve_route_access` /
`enforce_route_access`：

- 权限码默认由「方法→动作」（GET/HEAD→read，POST/PUT/PATCH→write，DELETE→delete）
  + 「路径首段→资源」自动推导，减少重复声明
- 特例（admin-only、仅登录、skip 掉用户 RBAC、命中但未登记资源时默认拒绝）用显式
  规则表覆盖，规则表命中优先于自动推导

## 前端集成模式（与框架无关）

前端**不做登录页**，登录由边缘 IdP 完成：

1. 页面加载时调 `GET /api/me`（走 `require_auth`）。成功→拿到 user/roles/permissions
   渲染界面；401/失败→视为未登录（正常情况下用户会先被边缘拦截到 IdP 登录页，
   走不到这一步，除非 AUTH_BYPASS 之外的配置缺失）
2. **顶栏必须有用户头像 + 下拉菜单 + 退出登录**，不是随便放个"退出"按钮就算完——这是
   每个接入本协议的前端都要做的固定件，不算可选装饰。最小要求：
   - 头像：优先取 `/api/me` 返回的 `user.avatar`/`avatar_url` 等字段，取不到时用姓名首字/
     末字兜底（`display_name`/`displayName`/`email` 顺位取，都取不到时显示"未登录"/"识别中"）；
     图片加载失败要有兜底（`@error` 回退成文字头像，不能留一个破图标）
   - 点头像弹下拉菜单，菜单里至少有姓名（+ email，如果有）和"退出登录"
   - 点击菜单外部要自动收起（`document.addEventListener('click', ..., true)` 判断
     `composedPath()` 是否命中菜单容器）
   - 登出按钮：`window.location.assign(`${API_BASE}/logout?returnTo=${encodeURIComponent(currentUrl)}`)`，
     服务端处理跳转到 IdP 登出地址
3. 权限码只用于前端隐藏/禁用按钮（UX 优化），**真正的授权判断必须在后端**；
   前端拿到的权限码来自 `/api/me` 返回的 `permissions`

参考实现：pm_system `frontend/src/app.ts`（`.user-area` / `_loadCurrentUser` / `_logout` /
`_renderUserAvatar`）+ `frontend/src/shared/api.ts`（`getCurrentUser` / `logoutUrl`）；
jxd_mrp `frontend/src/app-root.ts`（`.user-area`，单模块系统没有侧边栏，头像下拉菜单
直接挂在顶栏）+ `frontend/src/shared/api.ts`（`getMe` / `logoutUrl`）是按本系统精简后
的第二个落地实现，两边都能抄。

## 常见坑

- CF Access 登出必须用「本应用 origin」的 `/cdn-cgi/access/logout`，不要用
  team-domain 版本（`https://<team>.cloudflareaccess.com/cdn-cgi/access/logout`）——
  team-domain 需要它自己的 cookie，应用 session 里没有，会报 "No Access cookie found"
- Keycloak `post_logout_redirect_uri` 必须先在 Keycloak Admin Console → Client →
  Settings → "Valid post logout redirect URIs" 里加白名单，否则会被拒绝跳转
- Keycloak 登出不带 `id_token_hint` 会先出确认页，体验差；`id_token_hint` 要在
  登录时从 IdP 拿到的 `id_token` 存好（存在 session 层），登出时取出传入
- 多 worker / 多实例部署下内存缓存互相独立，权限变更生效有最多 5 分钟延迟属预期
  行为（重新登录立即生效）；不能接受这个延迟才需要换 Redis 共享缓存
- 命中 `/api/*` 但资源未登记权限码时，默认应该是**拒绝**而不是放行

## 接完后自检

把 `build_logout_url()`（或等价函数）的输出直接贴到浏览器：

- CF Access 应用：应看到 CF Access "You are logged out" 页，或按 `returnTo` 跳转
- Keycloak 应用：应看到 Keycloak 登出确认页（带 `id_token_hint` 则跳过确认）
