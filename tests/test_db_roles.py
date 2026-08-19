"""record_roles：单条批量 upsert + seen 来源的 TTL 跳写缓存（/api/me 高频调用的防 N+1）。
不连数据库：pool 换成记录 execute 调用的桩。"""

from contextlib import contextmanager

import pytest

from app import db


class _StubConn:
    def __init__(self, log):
        self._log = log

    def execute(self, sql, params=None):
        self._log.append((sql, params))
        return self


class _StubPool:
    def __init__(self, log):
        self._log = log

    @contextmanager
    def connection(self):
        yield _StubConn(self._log)


@pytest.fixture
def executed(monkeypatch):
    log = []
    monkeypatch.setattr(db, "pool", lambda: _StubPool(log))
    db._roles_seen_at.clear()
    yield log
    db._roles_seen_at.clear()


def test_seen_roles_written_as_single_batch(executed):
    db.record_roles(["finance", "hr", " finance "])
    assert len(executed) == 1
    sql, params = executed[0]
    assert "unnest" in sql
    assert params == (["finance", "hr"],)  # 去重去空白、排序


def test_seen_roles_skipped_within_ttl(executed):
    db.record_roles(["finance"])
    db.record_roles(["finance"])          # TTL 内重复出现：不再写库
    assert len(executed) == 1
    db.record_roles(["finance", "hr"])    # 只有新角色触发写
    assert len(executed) == 2
    assert executed[1][1] == (["hr"],)


def test_manual_roles_always_written(executed):
    db.record_roles(["ops"], source="manual")
    db.record_roles(["ops"], source="manual")  # 手动添加不走跳写缓存
    assert len(executed) == 2
    assert executed[0][1] == (["ops"], "manual")


def test_empty_names_no_write(executed):
    db.record_roles(["", "  "])
    assert executed == []
