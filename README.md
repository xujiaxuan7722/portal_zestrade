# ZesTrade 企业门户（portal.zestrade.com）

公司内部应用导航门户：员工登录后看到自己有权访问的应用入口；管理员可在后台对模块做
增删改、拖拽排序、按角色配置可见性。登录与权限完全依赖公司统一基础设施：

- **SSO**：Keycloak（经 oauth2-proxy），本应用不做登录页
- **RBAC**：统一权限服务 `rbac.bogoo.ai`，本应用不自己验 JWT / 管角色

接入协议遵循公司《RBAC/SSO 接入规范》（rbac-sso-integration skill），
`app/auth.py` 改自其 FastAPI 模板并按 Keycloak 链路启用。

## 目录结构

```
app/
  __init__.py 统一日志配置（LOG_LEVEL 环境变量调级别，默认 INFO）
  main.py     路由：/api/me、/api/modules(角色过滤)、/api/favorites(常用应用)、
              /api/announcements(横幅公告)、/api/admin/*（模块/角色池/公告 CRUD + 审计）、/logout
  auth.py     RBAC/SSO 接入层：JWT 提取 → 换权限（缓存 5 分钟）→ require_auth/role/permission
  db.py       PostgreSQL 数据层（psycopg3 + 连接池，DATABASE_URL 配置；生产漏配即拒绝启动）
migrations/
  001_baseline.sql  版本化表结构（应用启动时按编号顺序执行，schema_migrations 表记账）
tests/
  test_*.py   pytest 单测（auth 缓存/fail-closed、角色可见性、迁移物料检查；不连数据库）
frontend/
  index.html  门户（hash 路由双视图：#workbench 个人工作台=常用应用[星标自定义]+全部应用；
              #apps 全部应用=按分类分组的悬浮模块卡；横幅公告条；顶栏用户区）
  admin.html  管理后台（与门户同款风格；模块表格/弹窗/拖拽排序/角色多选/分类
              + 横幅公告管理 + 操作审计只读表格）
  common.css  两页共用样式（设计变量/顶栏/用户区固定件/侧边栏/卡片基础）
  common.js   两页共用脚本（fetchJSON/esc/iconContent/用户区固定件渲染与交互）
  icons.js    内置 SVG 图标库
scripts/
  migrate_sqlite_to_pg.py  旧 SQLite（portal.db）→ PostgreSQL 一次性迁移
```

## 数据结构（PostgreSQL）

| 表 | 用途 | 要点 |
|----|------|------|
| `modules` | 应用模块 | `visible_roles TEXT[]`（空=所有人可见）；`status`（normal/maintenance，维护中门户显示角标）；`owner_name` 负责人；`created_at/updated_at` |
| `known_roles` | 角色池 | 管理后台"可见角色"数据源；`source=seen` 为登录用户真实带回（保真），`manual` 为手动添加（未验证）；seen 可覆盖 manual |
| `user_favorites` | 常用应用 | 个人工作台"我的常用"；外键 `ON DELETE CASCADE`，删模块自动清收藏 |
| `announcements` | 横幅公告 | 门户顶部条；级别 info/warning、生效时间段（NULL=立即/长期）、可见角色复用模块规则 |
| `audit_logs` | 管理操作审计 | 管理端所有写操作自动记录（谁/何时/对什么/做了什么，JSONB 详情）；管理后台「操作审计」卡片展示最近 100 条（`GET /api/admin/audit`） |

分类保持 `modules.category` 自由字符串（不单独建表）：管理后台输入即创建，规模小、交互已定型。

表结构版本化管理：`migrations/*.sql` 按文件名顺序执行，已执行版本记录在
`schema_migrations` 表（应用启动时自动跑，失败则拒绝启动）。**改表结构 = 新增
编号递增的 SQL 文件**（如 `002_add_xxx.sql`），勿修改已执行过的文件；对已按旧方式
建过表的库，基线迁移（全部 `IF NOT EXISTS`）会直接标记通过。

### 从旧 SQLite 迁移

```bash
# 1. 建库（一次性，见 .env.example 注释）
# 2. 迁移旧数据（保留 id/排序/收藏）：
source .venv/bin/activate
DATABASE_URL=postgresql://portal:portal@127.0.0.1:5432/portal \
    python scripts/migrate_sqlite_to_pg.py    # PG 已有数据时加 --force 清空重导
# 3. 确认后归档旧库：mv portal.db portal.db.bak
```

## 本地开发

```bash
./run.sh          # 自动建 venv、装依赖，AUTH_BYPASS=true 启动
# 打开 http://127.0.0.1:8000        门户首页
# 打开 http://127.0.0.1:8000/admin  管理后台
```

AUTH_BYPASS=true 时认证层返回一个全权限测试用户（admin 角色），无需 RBAC / Keycloak
即可开发全部功能。需要本地 PostgreSQL 已就绪（建库见 `.env.example` 注释，
连接串默认 `postgresql://portal:portal@127.0.0.1:5432/portal`，该默认值仅
bypass 模式下生效）；空库首次启动自动建表（跑 migrations）并播种演示模块。

```bash
# 运行测试（不需要数据库）
pip install -r requirements-dev.txt && pytest
```

测试也在 GitHub Actions 上自动跑（`.github/workflows/ci.yml`，push/PR 触发）。

## 应用分类

- 模块有 `category` 字段（管理后台编辑，留空 = 归入"未分类"），门户首页侧边栏
  按分类分组：全部应用 + 各分类（含数量），数量按当前用户可见模块统计
- 预置分类：电商运营 / 供应链生产 / 产品设计 / 客户销售 / 协同办公，
  管理后台可自由输入新分类名，前端自动出现在侧边栏
- 旧 SQLite 库的分类数据由 `scripts/migrate_sqlite_to_pg.py` 一并迁入

## 角色可见性如何工作

- 模块的"可见角色"存 RBAC 中的**角色名字符串**，空 = 所有人可见
- `GET /api/modules` 在**后端**按当前用户角色过滤（前端隐藏只是体验优化，不是安全边界）；
  **admin 角色在首页可见全部启用模块**（便于总览自检），其他角色严格按可见角色过滤
- 删除角色池中的角色时，若仍被模块引用，后端返回 409 + 引用清单，前端确认后
  以 `?force=true` 重试——会先把该角色从相关模块的可见角色中移除再删，
  避免模块变成"谁也看不见"
- RBAC 暂无角色列表接口，管理后台下拉框数据来自本地 `known_roles` 角色池：
  - 用户每次访问 `/api/me`，其角色名自动收录（source=seen）
  - 管理员可在弹窗里手动添加（source=manual）
  - 将来 RBAC 提供角色接口后，把 `db.list_known_roles()` 的调用处换成远程拉取即可

## Docker 部署（公司统一口径，照 pm_system 模式）

单镜像（FastAPI 直接伺服前端静态文件）；compose 只启动 app 一个服务，
PostgreSQL / RBAC / oauth2-proxy 均为外部依赖，地址由 `.env` 注入。

| 文件 | 作用 |
|------|------|
| `Dockerfile` | 单镜像构建（python:3.13-slim + 依赖 + app/migrations/frontend） |
| `docker-compose.deploy.yml` | 生产 compose，仅启动 app，端口只绑 127.0.0.1 |
| `.env.docker.example` | 生产环境变量模板（`cp` 为 `.env` 后填写） |
| `docker/entrypoint.sh` | 容器启动入口（uvicorn，`BACKEND_PORT` 默认 8200） |
| `docker/docker-build.sh` | 构建镜像（透传代理变量与 `PIP_INDEX_URL`） |
| `docker/docker-deploy.sh` | `docker compose up -d` 启动/更新 |
| `docker/git-deploy.sh` | 服务器更新：git pull → 构建 → 重启 |

服务器首次部署：clone 仓库 → `cp .env.docker.example .env` 并填写 →
`./docker/docker-build.sh` → `./docker/docker-deploy.sh`。
之后每次更新只需 `./docker/git-deploy.sh`。

## 上线清单（按顺序）

1. [ ] 向管理员申请 **Service Token**（CF Zero Trust），并在 rbac.bogoo.ai
       对应 Application 的 Policy 中放行 → 填入 `RBAC_CLIENT_ID` / `RBAC_CLIENT_SECRET`
2. [ ] 请 RBAC 管理员登记本业务权限码 / 确认 `admin` 角色分配
3. [ ] 域名 `portal.zestrade.com` 接入 oauth2-proxy（Keycloak）保护
4. [ ] **配置 `OAUTH2_PROXY_SIGN_OUT_URL`（必配）**，如
       `https://portal.zestrade.com/oauth2/sign_out`——只登出 Keycloak 不清
       oauth2-proxy 会话的话，用户刷新页面仍是登录态；未走 oauth2-proxy 的
       特殊部署才用 `KEYCLOAK_CLIENT_ID` 兜底直连登出
5. [ ] Keycloak Client → "Valid post logout redirect URIs" 加入
       `https://portal.zestrade.com/*`（否则登出后无法跳回）
6. [ ] **`AUTH_BYPASS=false`**（或不设）——未配 RBAC 凭证且未开 bypass 时
       所有请求 401，属预期的 fail-closed 行为；防呆：bypass 与 RBAC 凭证
       同时配置时应用直接拒绝启动
7. [ ] 部署安全：应用端口只监听 `127.0.0.1`/内网，仅允许 oauth2-proxy 访问，
       并确认 proxy 会覆盖客户端伪造的 `X-Auth-Request-*` / `Authorization` 头；
       生产库单独建（`DATABASE_URL` 指向生产 PostgreSQL，强密码），
       **不要连开发库**（含测试数据），空库首次启动自动播种；
       生产模式（未开 bypass）漏配 `DATABASE_URL` 时应用直接拒绝启动，不回落本地默认库
8. [ ] 自检：浏览器直接访问 `/logout`，应回到统一登录页（proxy 会话已清）；
       `returnTo` 只接受站内地址，外部地址会被丢弃；改角色后权限最多 5 分钟
       生效（重新登录立即生效），为预期行为

## 已知边界

- 权限缓存为进程内存，多 worker 部署时各自独立；不能接受 5 分钟延迟才需要换 Redis
- 数据层为 PostgreSQL（psycopg 连接池），多 worker / 多实例部署无共享存储问题
- RBAC 调用失败/非 200 会记 WARNING 日志（`docker logs` 是排障入口——用户侧只会看到 401）
