"""instant-perf: 응답 캐시 — 히트=신선 동일·데이터 변경시 무효화(stale 0)·:memory: 우회. 키리스.

핵심 불변: 캐시는 **속도만** 바꾼다(결과 동일) · 어떤 write든 시그니처 변동 → 미스(재계산) ·
파일 없는 DB(:memory:·테스트)는 캐시 우회 → 거동 100% 동일.
"""

from __future__ import annotations

import sqlite3

from app.search.cache import cached, clear, data_signature
from app.store.db import get_connection


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)")
    conn.execute("INSERT INTO t (v) VALUES (1)")
    conn.commit()


def test_memory_db_bypasses_cache() -> None:
    # :memory:는 시그니처 None → 항상 compute(거동 동일·캐시 contamination 0).
    clear()
    conn = get_connection(":memory:")
    _seed(conn)
    assert data_signature(conn) is None
    calls = {"n": 0}

    def compute() -> int:
        calls["n"] += 1
        return 42

    assert cached("t", conn, "k", compute) == 42
    assert cached("t", conn, "k", compute) == 42
    assert calls["n"] == 2  # 매번 계산(캐시 우회)


def test_file_db_hit_skips_recompute(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # 파일 DB: 동일 키 2번째 호출은 히트(compute 미호출)·값 동일.
    clear()
    db = tmp_path / "c.db"
    conn = get_connection(db)
    _seed(conn)
    assert data_signature(conn) is not None
    calls = {"n": 0}

    def compute() -> str:
        calls["n"] += 1
        return f"val{calls['n']}"

    first = cached("tag", conn, "key", compute)
    second = cached("tag", conn, "key", compute)
    assert first == second == "val1"  # 히트 — 재계산 0(같은 값)
    assert calls["n"] == 1


def test_write_invalidates_cache(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # 데이터 변경(다른 커넥션 커밋) → 시그니처 변동 → 미스(신선 재계산). stale 0.
    clear()
    db = tmp_path / "c.db"
    conn = get_connection(db)
    _seed(conn)
    calls = {"n": 0}

    def compute() -> int:
        calls["n"] += 1
        return calls["n"]

    assert cached("tag", conn, "key", compute) == 1
    assert cached("tag", conn, "key", compute) == 1  # 히트
    # 외부 커넥션이 write+commit → DB/-wal 파일 변동
    writer = get_connection(db)
    writer.execute("INSERT INTO t (v) VALUES (2)")
    writer.commit()
    writer.close()
    sig_after = data_signature(conn)
    assert sig_after is not None
    # 캐시는 변경 후 미스 → 재계산(신선)
    assert cached("tag", conn, "key", compute) == 2
    assert calls["n"] == 2


def test_distinct_keys_isolated(tmp_path) -> None:  # type: ignore[no-untyped-def]
    clear()
    db = tmp_path / "c.db"
    conn = get_connection(db)
    _seed(conn)
    a = cached("tag", conn, "A", lambda: "a")
    b = cached("tag", conn, "B", lambda: "b")
    assert a == "a" and b == "b"  # 키별 독립(혼선 0)
    assert cached("tag", conn, "A", lambda: "X") == "a"  # A는 히트(X 아님)


def test_signature_changes_with_wal_write(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # WAL 모드 — 외부 커밋이 시그니처를 바꾼다(무효화 메커니즘의 토대).
    db = tmp_path / "c.db"
    conn = get_connection(db)
    _seed(conn)
    sig1 = data_signature(conn)
    w = get_connection(db)
    w.execute("INSERT INTO t (v) VALUES (9)")
    w.commit()
    w.close()
    sig2 = data_signature(conn)
    assert sig1 is not None and sig2 is not None and sig1 != sig2
