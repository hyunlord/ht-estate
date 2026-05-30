"""퍼지 조인 백필 — 동 필터→매칭, 무매치/모호 NULL, 멱등, 조인컬럼만 갱신."""

from __future__ import annotations

import sqlite3

from app.store.db import get_connection, init_db
from app.store.join_repo import backfill_matches


def _seed(conn: sqlite3.Connection) -> None:
    # 단지: dong은 NULL, legal_addr로 동 추출 경로를 탄다(현실적)
    complexes = [
        ("C1", "역삼자이아파트", "서울특별시 강남구 역삼동 711-1 역삼자이아파트"),
        ("C2", "역삼래미안펜타빌", "서울특별시 강남구 역삼동 757"),
        ("C3", "압구정미성2차", "서울특별시 강남구 압구정동 414"),
        ("C4", "현대5차", "서울특별시 강남구 압구정동 455"),
    ]
    conn.executemany(
        "INSERT INTO complex (complex_id, name, legal_addr) VALUES (?, ?, ?)", complexes
    )
    # 거래: complex_id NULL로 적재된 상태
    txns = [
        ("T1", "역삼자이", "역삼동"),       # → C1 (정규화 후 동일)
        ("T2", "미성2차", "압구정동"),      # → C3 (동/지역 prefix 포함)
        ("T3", "현대6차", "압구정동"),      # → 무매치 (현대5차와 번호가드 거절)
        ("T4", "없는단지", "역삼동"),       # → 무매치
        ("T5", "역삼자이", "청담동"),       # → 무매치 (동 필터: 청담동 후보 없음)
    ]
    conn.executemany(
        'INSERT INTO "transaction" (txn_id, apt_name_raw, legal_dong) VALUES (?, ?, ?)', txns
    )
    conn.commit()


def _complex_id(conn: sqlite3.Connection, txn_id: str) -> str | None:
    return conn.execute(
        'SELECT complex_id FROM "transaction" WHERE txn_id = ?', (txn_id,)
    ).fetchone()["complex_id"]


def test_backfill_matches_and_leaves_uncertain_null() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed(conn)

    stats = backfill_matches(conn)
    assert stats == {"matched": 2, "unmatched": 3, "total": 5}

    assert _complex_id(conn, "T1") == "C1"
    assert _complex_id(conn, "T2") == "C3"
    assert _complex_id(conn, "T3") is None  # 번호가드 거절
    assert _complex_id(conn, "T4") is None  # 무매치
    assert _complex_id(conn, "T5") is None  # 동 필터 — 청담동 후보 없음

    # 매칭된 행에 confidence 채워짐, 무매치는 NULL
    row = conn.execute('SELECT match_confidence FROM "transaction" WHERE txn_id = "T1"').fetchone()
    assert row["match_confidence"] is not None and row["match_confidence"] >= 0.85
    null_conf = conn.execute(
        'SELECT match_confidence FROM "transaction" WHERE txn_id = "T3"'
    ).fetchone()
    assert null_conf["match_confidence"] is None


def test_backfill_only_updates_join_columns() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed(conn)
    backfill_matches(conn)
    # apt_name_raw·legal_dong 등 비조인 컬럼은 불변
    row = conn.execute(
        'SELECT apt_name_raw, legal_dong FROM "transaction" WHERE txn_id = "T1"'
    ).fetchone()
    assert row["apt_name_raw"] == "역삼자이"
    assert row["legal_dong"] == "역삼동"


def test_backfill_is_idempotent() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed(conn)
    first = backfill_matches(conn)
    assert first["matched"] == 2

    # 재실행: 이미 매칭된 행은 안 건드림 → 남은 NULL(3건)만 재검토, 결과 동일
    second = backfill_matches(conn)
    assert second == {"matched": 0, "unmatched": 3, "total": 3}
    # 첫 매칭 보존
    assert _complex_id(conn, "T1") == "C1"
    assert _complex_id(conn, "T2") == "C3"
