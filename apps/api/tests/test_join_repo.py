"""퍼지 조인 백필 — bjd_code narrowing→매칭, 무매치/모호 NULL, 멱등, 조인컬럼만 갱신.

법정동코드: 역삼동=1168010100, 압구정동=1168011000, 청담동=1168010400,
수서동=1168011500, 대치동=1168010600.
"""

from __future__ import annotations

import sqlite3

from app.store.db import get_connection, init_db
from app.store.join_repo import backfill_matches


def _seed(conn: sqlite3.Connection) -> None:
    # 단지: bjd_code로 narrowing(legal_addr는 fallback 경로 검증용으로 함께 둠)
    complexes = [
        ("C1", "역삼자이아파트", "1168010100", "서울특별시 강남구 역삼동 711-1 역삼자이아파트"),
        ("C2", "역삼래미안펜타빌", "1168010100", "서울특별시 강남구 역삼동 757"),
        ("C3", "압구정미성2차", "1168011000", "서울특별시 강남구 압구정동 414"),
        ("C4", "현대5차", "1168011000", "서울특별시 강남구 압구정동 455"),
    ]
    conn.executemany(
        "INSERT INTO complex (complex_id, name, bjd_code, legal_addr) VALUES (?, ?, ?, ?)",
        complexes,
    )
    # 거래: complex_id NULL, bjd_code로 narrowing
    txns = [
        ("T1", "역삼자이", "역삼동", "1168010100"),    # → C1 (정규화 후 동일)
        ("T2", "미성2차", "압구정동", "1168011000"),   # → C3 (동/지역 prefix 포함)
        ("T3", "현대6차", "압구정동", "1168011000"),   # → 무매치 (현대5차와 번호가드 거절)
        ("T4", "없는단지", "역삼동", "1168010100"),    # → 무매치
        ("T5", "역삼자이", "청담동", "1168010400"),    # → 무매치 (bjd 그룹에 후보 없음)
    ]
    conn.executemany(
        'INSERT INTO "transaction" (txn_id, apt_name_raw, legal_dong, bjd_code) '
        "VALUES (?, ?, ?, ?)",
        txns,
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
    assert _complex_id(conn, "T5") is None  # bjd narrowing — 1168010400 그룹에 후보 없음

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


def test_bjd_narrowing_survives_dong_name_variance() -> None:
    # T0-4b 핵심: 동 *이름* 표기가 달라도 법정동 *코드*가 같으면 후보로 잡힌다.
    conn = get_connection(":memory:")
    init_db(conn)
    # 단지의 동(legal_addr)은 "수서동", 거래의 legal_dong은 "수서1동"(표기변이) —
    # 동 이름 narrowing이라면 그룹이 갈려 매칭 실패했을 케이스.
    conn.execute(
        "INSERT INTO complex (complex_id, name, bjd_code, legal_addr) VALUES "
        "('C6', '수서신동아', '1168011500', '서울특별시 강남구 수서동 750')"
    )
    conn.execute(
        'INSERT INTO "transaction" (txn_id, apt_name_raw, legal_dong, bjd_code) VALUES '
        "('T6', '신동아', '수서1동', '1168011500')"
    )
    conn.commit()
    stats = backfill_matches(conn)
    assert stats["matched"] == 1
    assert _complex_id(conn, "T6") == "C6"  # bjd_code 동등으로 매칭 — 동 표기변이 무관


def test_falls_back_to_dong_name_when_bjd_code_missing() -> None:
    # 구 데이터 등 bjd_code 없는 거래는 동 이름으로 fallback.
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO complex (complex_id, name, bjd_code, legal_addr) VALUES "
        "('C7', '은마', '1168010600', '서울특별시 강남구 대치동 316')"
    )
    conn.execute(
        'INSERT INTO "transaction" (txn_id, apt_name_raw, legal_dong, bjd_code) VALUES '
        "('T7', '은마', '대치동', NULL)"
    )
    conn.commit()
    stats = backfill_matches(conn)
    assert stats["matched"] == 1
    assert _complex_id(conn, "T7") == "C7"  # bjd 없음 → 동 이름 fallback으로 매칭
