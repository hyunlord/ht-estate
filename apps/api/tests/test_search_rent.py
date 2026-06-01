"""search_complexes deal_type 분기 — 전세/월세 테이블 조회·가격축 필터·매매 회귀 0 (키리스)."""

from __future__ import annotations

import sqlite3

from app.search.repo import Candidate, RepresentativeTrade, search_complexes
from app.search.spec import HardFilterSpec


def _ids(cands: list[Candidate]) -> set[str]:
    return {c.complex_id for c in cands}


def _by_id(cands: list[Candidate]) -> dict[str, Candidate]:
    return {c.complex_id: c for c in cands}


def _rep(cand: Candidate) -> RepresentativeTrade:
    assert cand.representative_trade is not None
    return cand.representative_trade


# ───────────────────────── deal_type 라우팅 ─────────────────────────


def test_jeonse_queries_rent_table_jeonse_only(search_db: sqlite3.Connection) -> None:
    cands = search_complexes(search_db, HardFilterSpec(deal_type="jeonse"))
    by = _by_id(cands)
    assert _ids(cands) == {"C1", "C2", "C3", "C4"}  # 필터 없음 → 전 단지(sale과 동일 의미)
    # C1·C2는 전세 거래 있음 → rep deposit, rent_type=jeonse, price None
    assert _rep(by["C1"]).rent_type == "jeonse"
    assert _rep(by["C1"]).deposit == 90000
    assert _rep(by["C1"]).monthly_rent == 0
    assert _rep(by["C1"]).price is None
    # C3은 월세만 → 전세 매칭 0 → rep 없음
    assert by["C3"].representative_trade is None
    assert by["C3"].transaction_count == 0


def test_monthly_queries_rent_table_monthly_only(search_db: sqlite3.Connection) -> None:
    by = _by_id(search_complexes(search_db, HardFilterSpec(deal_type="monthly")))
    # 월세 거래: C1·C3
    assert _rep(by["C1"]).rent_type == "monthly"
    assert _rep(by["C1"]).monthly_rent == 120
    assert _rep(by["C3"]).monthly_rent == 90
    # C2는 전세만 → 월세 매칭 0
    assert by["C2"].representative_trade is None


def test_deposit_filter_narrows_set(search_db: sqlite3.Connection) -> None:
    # 전세 보증금 ≥ 60000 → C1(90000)만(C2 50000 제외). SET을 좁힌다.
    cands = search_complexes(search_db, HardFilterSpec(deal_type="jeonse", deposit_min=60000))
    assert _ids(cands) == {"C1"}


def test_monthly_rent_filter(search_db: sqlite3.Connection) -> None:
    # 월세 ≥ 100 → C1(120)만(C3 90 제외).
    cands = search_complexes(search_db, HardFilterSpec(deal_type="monthly", monthly_rent_min=100))
    assert _ids(cands) == {"C1"}


def test_net_area_shared_across_deal_types(search_db: sqlite3.Connection) -> None:
    # 전용 80 이상 전세 → C1(84.97)·C2(76.79 제외). 공유 축.
    cands = search_complexes(search_db, HardFilterSpec(deal_type="jeonse", net_area_min=80))
    assert _ids(cands) == {"C1"}


def test_price_min_max_carries_deposit_for_rent(search_db: sqlite3.Connection) -> None:
    by = _by_id(search_complexes(search_db, HardFilterSpec(deal_type="jeonse")))
    assert by["C1"].price_min == 90000 and by["C1"].price_max == 90000  # 보증금 집계


# ───────────────────────── 매매 회귀 0 ─────────────────────────


def test_sale_is_default_and_unchanged(search_db: sqlite3.Connection) -> None:
    # deal_type 미지정 = sale. 기존 동작(price 필터·transaction 조회) 그대로.
    default = search_complexes(search_db, HardFilterSpec(price_min=150000))
    explicit = search_complexes(search_db, HardFilterSpec(deal_type="sale", price_min=150000))
    assert _ids(default) == _ids(explicit) == {"C2"}  # T0-6과 동일
    rep = _rep(_by_id(default)["C2"])
    assert rep.price == 200000  # 매매 가격
    assert rep.deposit is None and rep.monthly_rent is None and rep.rent_type is None


def test_sale_no_filter_returns_all(search_db: sqlite3.Connection) -> None:
    assert _ids(search_complexes(search_db, HardFilterSpec())) == {"C1", "C2", "C3", "C4"}
