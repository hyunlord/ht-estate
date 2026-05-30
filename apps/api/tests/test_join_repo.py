"""퍼지 조인 백필 — bjd_code narrowing→매칭, 무매치/모호 NULL, 멱등, 조인컬럼만 갱신.

법정동코드: 역삼동=1168010100, 압구정동=1168011000, 청담동=1168010400,
수서동=1168011500, 대치동=1168010600.
"""

from __future__ import annotations

import sqlite3

from app.store.db import get_connection, init_db
from app.store.join_repo import backfill_matches, recall_breakdown


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


# ───────── T0-4c 지번 매칭 ─────────


def test_jibun_rescues_name_below_threshold_unique_lot() -> None:
    # 이름은 임계 미달(0.727<0.85)이지만 같은 법정동·지번이면 회수.
    # MOLIT "압구정현대2차" vs K-apt "현대2차" — 동/지역 prefix 차이(같은 단지).
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, bjd_code, legal_addr) VALUES (?, ?, ?, ?)",
        [
            ("C1", "현대2차", "1168011000", "서울특별시 강남구 압구정동 489"),
            ("C2", "다른단지", "1168011000", "서울특별시 강남구 압구정동 999"),  # distractor
        ],
    )
    conn.execute(
        'INSERT INTO "transaction" (txn_id, apt_name_raw, legal_dong, bjd_code, jibun) VALUES '
        "('T1', '압구정현대2차', '압구정동', '1168011000', '489')"
    )
    conn.commit()

    # 지번 OFF → 이름만으로는 무매치(임계 미달)
    assert backfill_matches(conn, use_jibun=False)["matched"] == 0
    assert _complex_id(conn, "T1") is None

    # 지번 ON → 489 단일 점유 단지 C1로 회수, confidence는 지번 경로 상수
    stats = backfill_matches(conn, use_jibun=True)
    assert stats["matched"] == 1
    assert _complex_id(conn, "T1") == "C1"
    conf = conn.execute(
        'SELECT match_confidence FROM "transaction" WHERE txn_id = "T1"'
    ).fetchone()["match_confidence"]
    assert conf == 0.9


def test_jibun_does_not_rescue_brand_suffix_oversmatch_same_lot() -> None:
    # 청담대림 회귀: 같은 지번이라도 이름 타당성(0.615<0.70 floor) 미달이면 거절.
    # "청담대림이편한세상"(거래) ⊃ "청담대림"(단지)은 다른 단지(재건축) — 오매칭 금지.
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO complex (complex_id, name, bjd_code, legal_addr) VALUES "
        "('C1', '청담대림', '1168010400', '서울특별시 강남구 청담동 124')"
    )
    conn.execute(
        'INSERT INTO "transaction" (txn_id, apt_name_raw, legal_dong, bjd_code, jibun) VALUES '
        "('T1', '청담대림이편한세상', '청담동', '1168010400', '124')"  # 같은 지번!
    )
    conn.commit()
    stats = backfill_matches(conn, use_jibun=True)
    assert stats["matched"] == 0
    assert _complex_id(conn, "T1") is None  # 지번 일치해도 이름 타당성 미달 → NULL


def test_jibun_collision_two_complexes_one_lot_stays_null() -> None:
    # 지번 충돌(한 지번에 두 단지) + 이름 모호 → 모호갭 거름 → NULL.
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, bjd_code, legal_addr) VALUES (?, ?, ?, ?)",
        [
            ("C1", "삼성래미안1차", "1168010100", "서울특별시 강남구 역삼동 200"),
            ("C2", "삼성래미안2차", "1168010100", "서울특별시 강남구 역삼동 200"),  # 같은 지번
        ],
    )
    conn.execute(
        'INSERT INTO "transaction" (txn_id, apt_name_raw, legal_dong, bjd_code, jibun) VALUES '
        "('T1', '삼성래미안', '역삼동', '1168010100', '200')"  # 1차·2차 모두 포함 → 모호
    )
    conn.commit()
    stats = backfill_matches(conn, use_jibun=True)
    assert stats["matched"] == 0
    assert _complex_id(conn, "T1") is None


def test_jibun_number_guard_rejects_different_cha_same_lot() -> None:
    # 차수 다른 단지가 같은 지번이어도 번호가드(유사도 0.0<floor)로 거절.
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO complex (complex_id, name, bjd_code, legal_addr) VALUES "
        "('C1', '현대5차', '1168011000', '서울특별시 강남구 압구정동 455')"
    )
    conn.execute(
        'INSERT INTO "transaction" (txn_id, apt_name_raw, legal_dong, bjd_code, jibun) VALUES '
        "('T1', '현대6차', '압구정동', '1168011000', '455')"  # 같은 지번 가정
    )
    conn.commit()
    stats = backfill_matches(conn, use_jibun=True)
    assert stats["matched"] == 0
    assert _complex_id(conn, "T1") is None


def test_recall_breakdown_classifies_residual() -> None:
    # 이름만 매칭 후 남은 미매치를 지번-회수 가능 vs 구조적으로 분해.
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, bjd_code, legal_addr) VALUES (?, ?, ?, ?)",
        [
            ("C1", "현대2차", "1168011000", "서울특별시 강남구 압구정동 489"),  # 회수 대상 지번
            ("C2", "삼성1차", "1168011000", "서울특별시 강남구 압구정동 300"),
            ("C3", "삼성2차", "1168011000", "서울특별시 강남구 압구정동 300"),  # 충돌 지번
        ],
    )
    conn.executemany(
        'INSERT INTO "transaction" (txn_id, apt_name_raw, legal_dong, bjd_code, jibun) '
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("T1", "압구정현대2차", "압구정동", "1168011000", "489"),  # jibun_recoverable
            ("T2", "없는단지", "압구정동", "1168011000", "777"),  # structural(지번에 단지 없음)
            ("T3", "삼성", "압구정동", "1168011000", "300"),  # collision(300=C2·C3)
            ("T4", "주소불명", "압구정동", "1168011000", None),  # no_jibun
        ],
    )
    conn.commit()

    backfill_matches(conn, use_jibun=False)  # 이름만 — 위 4건 모두 NULL로 남음
    bd = recall_breakdown(conn)
    assert bd["total_unmatched"] == 4
    assert bd["jibun_recoverable"] == 1  # T1
    assert bd["structural"] == 1         # T2
    assert bd["collision"] == 1          # T3
    assert bd["no_jibun"] == 1           # T4

    # 회수 가능분은 use_jibun=True가 실제로 잡는 수와 일치해야(분해의 신뢰성)
    gained = backfill_matches(conn, use_jibun=True)["matched"]
    assert gained == bd["jibun_recoverable"]
