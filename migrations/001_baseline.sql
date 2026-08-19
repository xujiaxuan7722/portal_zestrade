-- 001_baseline — 基线：五张表 + 索引（与首版部署的表结构一致）
-- 全部 IF NOT EXISTS：对已按旧方式（db.create_schema）建过表的库直接标记通过。
-- 之后改表结构：新增编号递增的文件（如 002_add_xxx.sql），勿修改已执行过的文件。

CREATE TABLE IF NOT EXISTS modules (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name          TEXT        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 50),
  description   TEXT        NOT NULL DEFAULT '' CHECK (char_length(description) <= 200),
  icon          TEXT        NOT NULL DEFAULT '📦' CHECK (char_length(icon) <= 200),
  url           TEXT        NOT NULL CHECK (char_length(url) BETWEEN 1 AND 500),
  category      TEXT        NOT NULL DEFAULT '' CHECK (char_length(category) <= 50),
  sort_order    INTEGER     NOT NULL DEFAULT 0,
  visible_roles TEXT[]      NOT NULL DEFAULT '{}',      -- 空数组 = 所有人可见
  enabled       BOOLEAN     NOT NULL DEFAULT TRUE,
  status        TEXT        NOT NULL DEFAULT 'normal'
                            CHECK (status IN ('normal', 'maintenance')),
  owner_name    TEXT        NOT NULL DEFAULT '' CHECK (char_length(owner_name) <= 50),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS known_roles (
  name       TEXT PRIMARY KEY CHECK (char_length(name) BETWEEN 1 AND 50),
  source     TEXT        NOT NULL DEFAULT 'seen' CHECK (source IN ('seen', 'manual')),
  last_seen  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_favorites (
  user_id    TEXT    NOT NULL CHECK (char_length(user_id) <= 200),
  module_id  BIGINT  NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  position   INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, module_id)
);
CREATE INDEX IF NOT EXISTS idx_user_favorites_user ON user_favorites (user_id);

CREATE TABLE IF NOT EXISTS announcements (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  content       TEXT        NOT NULL CHECK (char_length(content) BETWEEN 1 AND 200),
  level         TEXT        NOT NULL DEFAULT 'info' CHECK (level IN ('info', 'warning')),
  starts_at     TIMESTAMPTZ,                            -- NULL = 立即生效
  ends_at       TIMESTAMPTZ,                            -- NULL = 长期有效
  visible_roles TEXT[]      NOT NULL DEFAULT '{}',      -- 空数组 = 所有人可见
  enabled       BOOLEAN     NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor       TEXT        NOT NULL DEFAULT '',          -- 操作人（display_name/email）
  action      TEXT        NOT NULL,                     -- create/update/delete/reorder/...
  target_type TEXT        NOT NULL,                     -- module/role/announcement
  target_id   TEXT        NOT NULL DEFAULT '',
  detail      JSONB       NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs (created_at DESC);
