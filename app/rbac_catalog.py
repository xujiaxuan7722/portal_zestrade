"""rbac_catalog.py — RBAC 权限目录（GET /api/service/catalog）的服务端客户端。

管理后台「可见权限」选择器的数据源，用 Service Token（CF-Access-Client-Id/Secret
两个头）调用。缓存策略：TTL 内直接用缓存；过期后带 If-None-Match 回源（304 只续期）；
请求失败（RBAC 偶发 502）重试一次，仍失败回退旧缓存，无缓存才抛错。
AUTH_BYPASS 模式返回内置演示目录（本地开发不配 Service Token，防呆不允许并存）。
"""

import logging
import time
from typing import Any, Optional

from . import auth

logger = logging.getLogger(__name__)

CATALOG_TTL = 600  # 目录变更低频，10 分钟内不回源

_cache: Optional[dict[str, Any]] = None
_etag: Optional[str] = None
_fresh_until: float = 0.0

# 结构对齐真实接口（systems[]/permissions[]/roles[]，permissions 元素含
# code/system/description/roles），含无前缀历史码与 system 为 null 的边角
_BYPASS_CATALOG: dict[str, Any] = {
    "systems": [
        {"name": "pm_system", "permission_count": 3},
        {"name": "platform", "permission_count": 2},
        {"name": None, "permission_count": 1},
    ],
    "permissions": [
        {"code": "pm_system:read:sku", "system": "pm_system", "description": "查看 SKU", "roles": []},
        {"code": "pm_system:read:spu", "system": "pm_system", "description": "查看 SPU", "roles": []},
        {"code": "pm_system:*", "system": "pm_system", "description": "PM 系统全部权限", "roles": []},
        {"code": "*", "system": "platform", "description": "Wildcard: all permissions", "roles": ["admin"]},
        {"code": "read:users", "system": "platform", "description": "历史无前缀码示例", "roles": []},
        {"code": "demo:orphan", "system": None, "description": "无归属系统示例", "roles": []},
    ],
    "roles": [],
}


async def get_catalog() -> dict[str, Any]:
    global _cache, _etag, _fresh_until
    if auth.AUTH_BYPASS:
        return _BYPASS_CATALOG
    if _cache is not None and time.time() < _fresh_until:
        return _cache

    headers = {
        "CF-Access-Client-Id": auth.RBAC_CLIENT_ID,
        "CF-Access-Client-Secret": auth.RBAC_CLIENT_SECRET,
    }
    if _etag:
        headers["If-None-Match"] = _etag
    url = f"{auth.RBAC_API_URL}/api/service/catalog"

    last_err = ""
    for _attempt in range(2):
        try:
            resp = await auth._get_http_client().get(url, headers=headers)
            if resp.status_code == 304 and _cache is not None:
                _fresh_until = time.time() + CATALOG_TTL
                return _cache
            if resp.status_code == 200:
                _cache = resp.json()
                _etag = resp.headers.get("etag")
                _fresh_until = time.time() + CATALOG_TTL
                return _cache
            last_err = f"HTTP {resp.status_code}"
        except Exception as exc:
            last_err = repr(exc)

    if _cache is not None:
        logger.warning("RBAC catalog 拉取失败（%s），使用旧缓存兜底", last_err)
        return _cache
    raise RuntimeError(f"RBAC catalog 不可用且无缓存：{last_err}")
