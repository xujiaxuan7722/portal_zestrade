-- 002_module_requires — 应用可见性从「配角色」改为「配权限码」（20260818 新需求任务一）
-- modules 加 requires TEXT[]：空数组 = 所有登录用户可见；元素为完整权限码 /
-- 裸系统前缀 / "<system>:*" 系统通配。RBAC 是唯一事实源，不建用户-应用授权表。
-- visible_roles 列保留不删（存量数据迁移方案待需求方确认后再出 003）；
-- 仅把明确的「admin 角色可见」翻译为 admin:* 权限码，其余非空角色配置需人工重配。

ALTER TABLE modules ADD COLUMN IF NOT EXISTS requires TEXT[] NOT NULL DEFAULT '{}';

UPDATE modules
   SET requires = ARRAY['admin:*']
 WHERE visible_roles = ARRAY['admin']::text[] AND requires = '{}';
