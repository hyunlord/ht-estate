"""테스트 공용 fixture 로더.

fixture는 data.go.kr 문서화 스키마 기반으로 수작업 구성한 샘플 응답이다
(라이브 호출 금지·키 불필요). 정확한 태그명은 T0-3 실적재에서 라이브 재검증.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from app.store.db import get_connection, init_db

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture() -> Callable[[str], str]:
    def _load(name: str) -> str:
        return (FIXTURES_DIR / name).read_text(encoding="utf-8")

    return _load


# 강남 4단지 + 거래 — T0-6 hard filter 테스트 공용 시드.
# has_gym은 일부러 1/0/NULL 섞어 둔다(R1: 필터가 무시해야 함).
_COMPLEXES = [
    # id, name, approval, p_ratio, p_under, household, lat, lng, has_gym, source_url
    ("C1", "역삼자이", "2016-06-22", 1.5, 615, 408, 37.500, 127.040, 0, "https://k-apt/C1"),
    ("C2", "은마", "1979-08-01", 0.8, 0, 4424, 37.501, 127.060, 1, "https://k-apt/C2"),
    ("C3", "신축단지", "2022-03-10", 1.8, 300, 200, 37.490, 127.050, None, "https://k-apt/C3"),
    ("C4", "좌표없음단지", "2015-01-01", 1.2, 100, 300, None, None, 0, "https://k-apt/C4"),
]
_TRANSACTIONS = [
    # complex_id, net_area, price(만원), floor, deal_date, match_confidence
    ("C1", 84.97, 142000, 12, "2025-04-15", 1.0),
    ("C1", 59.92, 98000, 7, "2025-03-01", 1.0),
    ("C2", 76.79, 200000, 5, "2025-04-10", 0.7),  # 저신뢰 매칭
    ("C3", 84.0, 130000, 10, "2025-02-01", 0.95),
    (None, 50.0, 50000, 3, "2025-04-20", None),  # 구조적 미매치(complex_id NULL) — 절대 후보 아님
]
# 전월세 거래(P2-2) — 전세(월세 0)·월세 섞음. complex_id 직접 부여(조인은 P2-1에서 검증).
_RENT_TRANSACTIONS = [
    # complex_id, net_area, deposit, monthly_rent, rent_type, floor, deal_date, match_confidence
    ("C1", 84.97, 90000, 0, "jeonse", 12, "2025-04-18", 1.0),   # 전세
    ("C1", 59.92, 20000, 120, "monthly", 7, "2025-03-05", 1.0),  # 월세
    ("C2", 76.79, 50000, 0, "jeonse", 5, "2025-04-11", 0.7),     # 전세(저신뢰)
    ("C3", 84.0, 30000, 90, "monthly", 10, "2025-02-03", 0.95),  # 월세
]


@pytest.fixture
def search_db() -> sqlite3.Connection:
    """hard filter 테스트용 :memory: DB — 단지+거래 시드."""
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, approval_date, parking_ratio, "
        "parking_underground, household_count, lat, lng, has_gym, source_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        _COMPLEXES,
    )
    conn.executemany(
        'INSERT INTO "transaction" '
        "(txn_id, complex_id, net_area, price, floor, deal_date, match_confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(f"T{i}", *t) for i, t in enumerate(_TRANSACTIONS)],
    )
    conn.executemany(
        "INSERT INTO rent_transaction "
        "(txn_id, complex_id, net_area, deposit, monthly_rent, rent_type, floor, "
        "deal_date, match_confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(f"R{i}", *t) for i, t in enumerate(_RENT_TRANSACTIONS)],
    )
    conn.commit()
    return conn
