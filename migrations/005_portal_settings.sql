-- 005_portal_settings — 门户全局设置（键值表）。
-- 首个用途：category_order = 门户首页分类块的自定义先后（JSON 字符串数组）。
-- 未配置时门户按分类名拼音排序；配置了的分类按此顺序排在前，新出现的分类
-- 仍按拼音接在其后，"未分类"永远最后。后续其他全局开关也放这里，不再加表。

CREATE TABLE IF NOT EXISTS portal_settings (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
