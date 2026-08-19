-- 003_permcode_cleanup — 可见性全面权限码化收尾（需求方确认后执行）：
--   公告改配权限码；删除角色制全部残留（modules/announcements 的 visible_roles
--   列、known_roles 角色池表）。
-- 公告的角色配置无法自动翻译为权限码：直接删列，受限公告需在新界面重配
-- （公告为短时效内容，损失可接受；应用侧的 admin 映射已在 002 回填）。

ALTER TABLE announcements ADD COLUMN IF NOT EXISTS requires TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE announcements DROP COLUMN IF EXISTS visible_roles;
ALTER TABLE modules DROP COLUMN IF EXISTS visible_roles;
DROP TABLE IF EXISTS known_roles;
