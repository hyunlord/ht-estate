"""search_complexes — 각 필터 차원·조인·저신뢰·gym 부재(R1)·빈결과·limit·대표거래."""

from __future__ import annotations

import sqlite3
from datetime import date

from app.search.repo import Candidate, search_complexes
from app.search.spec import HardFilterSpec


def _ids(cands: list[Candidate]) -> set[str]:
    return {c.complex_id for c in cands}


def test_no_filter_returns_all_complexes(search_db: sqlite3.Connection) -> None:
    # 빈 spec → 전 단지(구조적 미매치 NULL 거래는 단지가 아니라 후보 아님)
    cands = search_complexes(search_db, HardFilterSpec())
    assert _ids(cands) == {"C1", "C2", "C3", "C4"}


def test_filter_approval_year(search_db: sqlite3.Connection) -> None:
    cands = search_complexes(search_db, HardFilterSpec(approval_year_min=2010))
    assert _ids(cands) == {"C1", "C3", "C4"}  # 은마(1979) 제외


def test_filter_parking_ratio(search_db: sqlite3.Connection) -> None:
    cands = search_complexes(search_db, HardFilterSpec(parking_ratio_gte=1.3))
    assert _ids(cands) == {"C1", "C3"}  # 은마 0.8·C4 1.2 제외


def test_filter_parking_underground(search_db: sqlite3.Connection) -> None:
    cands = search_complexes(search_db, HardFilterSpec(parking_underground=True))
    assert _ids(cands) == {"C1", "C3", "C4"}  # 은마(지하 0) 제외


def test_filter_household(search_db: sqlite3.Connection) -> None:
    spec = HardFilterSpec(household_count_min=300, household_count_max=1000)
    assert _ids(search_complexes(search_db, spec)) == {"C1", "C4"}  # 은마 4424·C3 200 제외


def test_filter_bbox_excludes_null_coords(search_db: sqlite3.Connection) -> None:
    cands = search_complexes(
        search_db, HardFilterSpec(min_lat=37.48, max_lat=37.51, min_lng=127.03, max_lng=127.07)
    )
    assert _ids(cands) == {"C1", "C2", "C3"}  # C4(lat NULL) 제외


def test_txn_filter_requires_matching_trade(search_db: sqlite3.Connection) -> None:
    # net_area 80~90 매칭 거래 있는 단지만 (EXISTS). C1(84.97)·C3(84.0)
    cands = search_complexes(search_db, HardFilterSpec(net_area_min=80.0, net_area_max=90.0))
    assert _ids(cands) == {"C1", "C3"}  # 은마(76.79)·C4(거래없음) 제외


def test_txn_filter_price(search_db: sqlite3.Connection) -> None:
    cands = search_complexes(search_db, HardFilterSpec(price_min=150000))
    assert _ids(cands) == {"C2"}  # 200000만 ≥150000


def test_txn_filter_deal_since(search_db: sqlite3.Connection) -> None:
    cands = search_complexes(search_db, HardFilterSpec(deal_since=date(2025, 4, 1)))
    assert _ids(cands) == {"C1", "C2"}  # 4월 거래 있는 단지


def test_low_confidence_match_is_surfaced(search_db: sqlite3.Connection) -> None:
    # 저신뢰(0.7) 매칭도 제외 안 하고 confidence를 실어 배지 가능케 (설계 §5.1)
    cands = search_complexes(search_db, HardFilterSpec(price_min=150000))
    c2 = next(c for c in cands if c.complex_id == "C2")
    assert c2.representative_trade is not None
    assert c2.representative_trade.match_confidence == 0.7


def test_representative_trade_and_aggregates(search_db: sqlite3.Connection) -> None:
    cands = search_complexes(search_db, HardFilterSpec())
    c1 = next(c for c in cands if c.complex_id == "C1")
    assert c1.transaction_count == 2
    assert c1.price_min == 98000
    assert c1.price_max == 142000
    # 대표거래 = 최근 1건 (2025-04-15)
    assert c1.representative_trade is not None
    assert c1.representative_trade.deal_date == "2025-04-15"
    assert c1.representative_trade.price == 142000
    # 필터된 complex 속성도 동봉(카드 렌더용)
    assert c1.parking_ratio == 1.5
    assert c1.source_url == "https://k-apt/C1"


def test_complex_without_matching_trade_has_none_rep(search_db: sqlite3.Connection) -> None:
    # C4는 거래 없음 → 대표거래 None, count 0 (txn 필터 없으면 후보엔 포함)
    cands = search_complexes(search_db, HardFilterSpec())
    c4 = next(c for c in cands if c.complex_id == "C4")
    assert c4.representative_trade is None
    assert c4.transaction_count == 0


def test_gym_is_never_filtered(search_db: sqlite3.Connection) -> None:
    # R1 회귀: has_gym 값(0/1/NULL)에 무관하게 모두 후보. gym으로 거르지 않는다.
    cands = search_complexes(search_db, HardFilterSpec())
    assert {"C1", "C2", "C3", "C4"} <= _ids(cands)  # has_gym=1(C2)·0(C1)·NULL(C3) 모두 포함


def test_empty_result_is_graceful(search_db: sqlite3.Connection) -> None:
    assert search_complexes(search_db, HardFilterSpec(approval_year_min=2030)) == []


def test_limit_applied(search_db: sqlite3.Connection) -> None:
    cands = search_complexes(search_db, HardFilterSpec(limit=1))
    assert len(cands) == 1


def test_neutral_sort_by_recent_deal(search_db: sqlite3.Connection) -> None:
    # 대표거래 최근일 desc 정렬 — 거래 있는 단지가 거래 없는 C4보다 앞
    cands = search_complexes(search_db, HardFilterSpec())
    assert cands[-1].complex_id == "C4"  # 거래 없음 → 맨 뒤
