# ZesTrade 企业门户 · 试跑包

换一台装了 Docker（含 compose 插件）的机器，三步跑起来，不依赖原开发机上的任何东西。

## 包内文件

| 文件 | 作用 |
|---|---|
| `portal-zestrade.tar.gz` | 应用镜像（FastAPI + 前端静态文件 + 数据库迁移脚本）。**不要手动解压**，`docker load` 直接读取 |
| `docker-compose.yml` | 方式 A 一键启动：应用容器 + 自带 PostgreSQL 容器 |
| `docker-compose.remote-db.yml` | 方式 B：只起应用，连开发机 PostgreSQL |
| `docker-compose.prod.yml` + `.env.prod.example` | 方式 C：正式接入，挂在公司 oauth2-proxy 后（见下）|
| `seed/portal.sql` | 演示数据（应用模块、自定义规则等），数据库首次启动自动导入 |
| `README.md` | 本文件 |

## 启动

```bash
docker load -i portal-zestrade.tar.gz     # 导入镜像，得到 portal-zestrade:mentor
docker compose up -d                      # 启动数据库 + 应用
```

浏览器打开 <http://localhost:8200>（门户首页），<http://localhost:8200/admin>（管理后台）。

8200 被占用时换端口：`HOST_PORT=8300 docker compose up -d`。

## 方式 B：不起数据库，直接连开发机的 PostgreSQL

同一局域网、开发机（192.168.66.112）开着时可用，数据与开发机演示环境实时同步：

```bash
docker load -i portal-zestrade.tar.gz
docker compose -f docker-compose.remote-db.yml up -d
```

两种方式二选一即可；方式 A 自带数据库，离线可跑。

## 方式 C：正式接入（portal.zestrade.com，钉钉扫码登录）

portal.zestrade.com 已解析到公司服务器并配好了代理入口（当前 502 = 后面还没有应用）。
在那台服务器上：

```bash
docker load -i portal-zestrade.tar.gz
cp .env.prod.example .env.prod          # 填 RBAC 凭证；DATABASE_URL 指向生产库；端口按代理配置改
docker compose -f docker-compose.prod.yml up -d
```

应用只监听 127.0.0.1:8200，由现有反向代理把 portal.zestrade.com 转到这个端口。
登录走 sso.zestrade.com 的共享 oauth2-proxy，与 pm / pf / crm 同一套，RBAC 按钉钉身份自动匹配，
不需要额外录入用户；管理后台只对持有 `*`（admin 角色）或 `portal:manage:console` 的人开放。
自检：打开 https://portal.zestrade.com 应跳统一登录页；登录后点右上角"退出登录"应回到登录页。

## 停止 / 重置

```bash
docker compose down        # 停止，保留数据
docker compose down -v     # 停止并清空数据库；下次 up 会重新导入 seed
```

## 说明

- **演示模式**：`AUTH_BYPASS=true` 绕过了公司统一登录，打开即为"开发测试用户"（admin，全权限）。
  **没有登录态，所以"退出登录"不生效（只回首页提示）、不能切换用户、子系统链接仍会各自要求登录**；
  这是演示模式的固有限制，不是故障。要模拟别的角色，改 compose 里的
  `AUTH_BYPASS_NAME` / `AUTH_BYPASS_ROLES`（如 `finance`）后 `docker compose up -d` 重建即可。
  正式接入：`AUTH_BYPASS=false` + 填 RBAC 凭证，并把门户挂到 oauth2-proxy + Keycloak 后面，
  登录/退出/切换用户与子系统免登由 SSO 会话统一提供，RBAC 决定每个人看到哪些应用。
- **权限目录**：管理后台"可见性"里 RBAC 目录部分（pm_system / platform 分组）在演示模式下是内置样例，
  接入公司 RBAC 后显示真实目录；"门户自定义规则"分组是门户自己存的。
- **数据库**：演示用 compose 自带的 PostgreSQL；生产改为外部 PostgreSQL，连接串由 `.env` 的
  `DATABASE_URL` 注入。表结构由应用启动时自动迁移。
- 正式上线链路、上线清单见仓库 `README.md` 与 `docs/部署步骤.docx`。
