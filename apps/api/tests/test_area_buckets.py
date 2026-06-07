"""평형(전용면적 버킷) 집계(detail-1) — single-linkage 클러스터링·deal_type 금액축.

읽기전용 집계: 노이즈(decimal) 흡수 + 과분할 금지를 net_area 분포로 검증. 거래 생성 없음.
shared search_db(C1=다평형·C2=단일평형)와 자체 시드(노이즈/타입경계)를 함께 쓴다.
"""

from __future__ import annotations

import sqlite3

from app.search.repo import search_complexes
from app.search.spec import HardFilterSpec
from app.store.db import get_connection, init_db


def _seed_sale(trades: list[tuple[float, int, str]]) -> sqlite3.Connection:
    """한 단지(SX) + 주어진 매매 거래(net_area, price, deal_date)로 :memory: DB 구성."""
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO complex (complex_id, name, source_url) VALUES ('SX', '테스트단지', 'https://k-apt/SX')"
    )
    conn.executemany(
        'INSERT INTO "transaction" '
        "(txn_id, complex_id, net_area, price, floor, deal_date, match_confidence) "
        "VALUES (?, 'SX', ?, ?, 1, ?, 1.0)",
        [(f"T{i}", na, pr, dt) for i, (na, pr, dt) in enumerate(trades)],
    )
    conn.commit()
    return conn


def _buckets(conn: sqlite3.Connection, spec: HardFilterSpec | None = None):
    cands = search_complexes(conn, spec or HardFilterSpec())
    cand = next(c for c in cands if c.complex_id == "SX")
    assert cand.area_buckets is not None
    return cand.area_buckets


# ── shared fixture: 다평형(C1) · 단일평형(C2) ──
def test_single_area_building_one_bucket(search_db: sqlite3.Connection) -> None:
    # C2 = 76.79㎡ 단일 → 버킷 1개(과분할 없음)
    c2 = next(c for c in search_complexes(search_db, HardFilterSpec()) if c.complex_id == "C2")
    assert c2.area_buckets is not None
    assert len(c2.area_buckets) == 1
    assert c2.area_buckets[0].transaction_count == 1
    assert c2.area_buckets[0].net_area == 76.79


def test_multi_area_building_breaks_down(search_db: sqlite3.Connection) -> None:
    # C1 = 84.97 + 59.92 (gap 25㎡, 명백히 다른 평형) → 2 버킷, 평형순(작은→큰) 정렬
    c1 = next(c for c in search_complexes(search_db, HardFilterSpec()) if c.complex_id == "C1")
    assert c1.area_buckets is not None
    assert [b.net_area for b in c1.area_buckets] == [59.92, 84.97]
    assert all(b.transaction_count == 1 for b in c1.area_buckets)
    # 큰 평형 버킷의 최근 거래 가격축(매매=price)
    big = c1.area_buckets[1]
    assert big.recent_amount == 142000
    assert big.recent_deal_date == "2025-04-15"


def test_rent_axis_uses_deposit(search_db: sqlite3.Connection) -> None:
    # deal_type=jeonse → 금액축 deposit, recent_rent_type 채워짐(전월세 테이블 분리)
    c1 = next(
        c
        for c in search_complexes(search_db, HardFilterSpec(deal_type="jeonse"))
        if c.complex_id == "C1"
    )
    assert c1.area_buckets is not None
    assert len(c1.area_buckets) == 1  # 전세는 84.97 한 건만(59.92는 monthly)
    b = c1.area_buckets[0]
    assert b.net_area == 84.97
    assert b.recent_amount == 90000  # deposit
    assert b.recent_rent_type == "jeonse"


def test_monthly_axis_carries_monthly_rent(search_db: sqlite3.Connection) -> None:
    c1 = next(
        c
        for c in search_complexes(search_db, HardFilterSpec(deal_type="monthly"))
        if c.complex_id == "C1"
    )
    assert c1.area_buckets is not None
    b = c1.area_buckets[0]
    assert b.net_area == 59.92
    assert b.recent_amount == 20000  # deposit
    assert b.recent_monthly_rent == 120
    assert b.recent_rent_type == "monthly"


# ── 자체 시드: 노이즈 흡수 · 과분할 금지 (net_area 분포 근거) ──
def test_decimal_noise_merges_into_one_bucket() -> None:
    # 84.93/84.95/84.97 — decimal 노이즈(같은 평형). 1 버킷, count=3, 대표=최다(tie→최근)
    conn = _seed_sale(
        [
            (84.93, 140000, "2025-01-01"),
            (84.95, 145000, "2025-02-01"),
            (84.97, 150000, "2025-03-01"),
        ]
    )
    buckets = _buckets(conn)
    assert len(buckets) == 1
    b = buckets[0]
    assert b.transaction_count == 3
    assert b.amount_min == 140000
    assert b.amount_max == 150000
    assert b.recent_amount == 150000  # 최근(2025-03)
    assert b.recent_deal_date == "2025-03-01"


def test_same_type_ab_variant_merges() -> None:
    # 84.63 / 86.60 (gap 1.97㎡ < clamp(5%·84.6→4.0)) — 같은 타입 A/B 변형 → 1 버킷
    conn = _seed_sale([(84.6348, 347000, "2025-01-01"), (86.6002, 390000, "2025-02-01")])
    assert len(_buckets(conn)) == 1


def test_distinct_large_types_split() -> None:
    # 156.66(47평) / 163.86(49평) (gap 7.2㎡ > 4.0 캡) — 다른 타입 → 2 버킷(과병합 방지)
    conn = _seed_sale([(156.66, 268000, "2025-01-01"), (163.86, 309000, "2025-02-01")])
    assert len(_buckets(conn)) == 2


def test_small_units_stay_distinct() -> None:
    # 35.22 / 39.13 (gap 3.9㎡, 소형에선 5%≈1.7㎡ 임계 초과) — 다른 소형 평형 → 2 버킷
    conn = _seed_sale([(35.22, 31000, "2025-01-01"), (39.13, 35000, "2025-02-01")])
    assert len(_buckets(conn)) == 2


def test_representative_is_most_traded_area() -> None:
    # 같은 버킷 내 84.93×2 / 84.97×1 → 대표 net_area = 최다거래(84.93)
    conn = _seed_sale(
        [
            (84.93, 140000, "2025-01-01"),
            (84.93, 141000, "2025-04-01"),
            (84.97, 150000, "2025-02-01"),
        ]
    )
    buckets = _buckets(conn)
    assert len(buckets) == 1
    assert buckets[0].net_area == 84.93


def test_null_net_area_excluded_from_buckets() -> None:
    conn = _seed_sale([(84.97, 142000, "2025-04-15")])
    conn.execute(
        "INSERT INTO \"transaction\" (txn_id, complex_id, net_area, price, floor, deal_date, "
        "match_confidence) VALUES ('TN', 'SX', NULL, 99000, 1, '2025-05-01', 1.0)"
    )
    conn.commit()
    buckets = _buckets(conn)
    assert len(buckets) == 1  # NULL net_area 거래는 버킷에서 제외
    assert buckets[0].transaction_count == 1
