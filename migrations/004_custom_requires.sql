-- 004_custom_requires — 门户自定义准入规则（跨模块/公告复用的手动权限码条目）。
-- 不是权限码目录：RBAC 仍是唯一事实源。此表只存管理员在门户里手填的 requires
-- 写法（裸系统前缀 / <system>:* 通配 / RBAC 尚未登记的完整码），供所有应用与
-- 公告的"可见性"选择器共同展示与勾选；不产生任何权限。
-- 删除某条时应用层会同步从 modules/announcements 的 requires 里移除该码。

CREATE TABLE IF NOT EXISTS custom_requires (
    code        TEXT PRIMARY KEY,
    note        TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
