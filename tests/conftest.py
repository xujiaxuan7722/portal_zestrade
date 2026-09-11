"""测试公共设置。

必须在 import app.* 之前把环境摆成"本地开发"形态：
  - AUTH_BYPASS=true：绕过 RBAC（fail-closed 用例里再 monkeypatch 回 False）
  - 清掉 RBAC 凭证：bypass 与凭证并存会触发 auth 的防呆 RuntimeError
  - 清掉 DATABASE_URL 等：测试不连数据库（db 层函数全部 monkeypatch；
    TestClient 不用 with，不触发 lifespan/init_db）
"""

import os

os.environ["AUTH_BYPASS"] = "true"
for _k in (
    "RBAC_CLIENT_ID",
    "RBAC_CLIENT_SECRET",
    "DATABASE_URL",
    "AUTH_BYPASS_NAME",
    "AUTH_BYPASS_ROLES",
    "AUTH_BYPASS_EMAIL",
    "IDP",
    "OAUTH2_PROXY_SIGN_OUT_URL",
    "OAUTH2_PROXY_URL",
    "KEYCLOAK_CLIENT_ID",
):
    os.environ.pop(_k, None)
