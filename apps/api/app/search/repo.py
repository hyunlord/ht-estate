"""search_complexes — HardFilterSpec → complex ⨝ transaction → 후보(이진 in/out).

설계 §7: hard 조건은 이진 in/out. soft 점수·랭킹 없음(중립 정렬: 대표거래 최근일 desc).
저신뢰 매칭은 제외하지 않고 match_confidence를 후보에 실어 "추정 매칭" 배지를 가능케 한다(§5.1).
gym은 어디에도 없다(R1 — Tier-2 소관).
"""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel

from app.search.gym import GymSummary
from app.search.pet import PetSummary
from app.search.spec import HardFilterSpec


class RepresentativeTrade(BaseModel):
    """범위 내 최근 1건 — 카드의 실거래 줄. deal_type별 가격축만 채워짐(나머지 None)."""

    net_area: float | None
    price: int | None  # 만원 (매매)
    deposit: int | None = None  # 만원 (전세·월세 보증금)
    monthly_rent: int | None = None  # 만원 (월세)
    rent_type: str | None = None  # 'jeonse' | 'monthly' (전월세만)
    floor: int | None
    deal_date: str | None  # ISO
    match_confidence: float | None  # 추정매칭 배지용 (저신뢰면 낮음 / NULL=직접확인불가)


class Candidate(BaseModel):
    """후보 단지 — 필터된 complex 속성 + 거래 요약. 카드 ✓ 렌더용."""

    complex_id: str
    name: str | None
    approval_date: str | None
    parking_ratio: float | None
    parking_underground: int | None
    household_count: int | None
    lat: float | None
    lng: float | None
    source_url: str | None
    transaction_count: int
    price_min: int | None
    price_max: int | None
    representative_trade: RepresentativeTrade | None
    # Tier-2 soft(R1: hard filter 아님 — 후보 산출 후 attach_gym/attach_pet로 부착). repo는 안 채움.
    gym: GymSummary | None = None
    pet: PetSummary | None = None


def _complex_where(spec: HardFilterSpec) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if spec.approval_year_min is not None:
        clauses.append("CAST(substr(c.approval_date, 1, 4) AS INTEGER) >= ?")
        params.append(spec.approval_year_min)
    if spec.approval_year_max is not None:
        clauses.append("CAST(substr(c.approval_date, 1, 4) AS INTEGER) <= ?")
        params.append(spec.approval_year_max)
    if spec.parking_ratio_gte is not None:
        clauses.append("c.parking_ratio >= ?")
        params.append(spec.parking_ratio_gte)
    if spec.parking_underground:  # True일 때만 — 지하주차 보유 요구
        clauses.append("c.parking_underground > 0")
    if spec.household_count_min is not None:
        clauses.append("c.household_count >= ?")
        params.append(spec.household_count_min)
    if spec.household_count_max is not None:
        clauses.append("c.household_count <= ?")
        params.append(spec.household_count_max)
    if spec.has_bbox:
        clauses.append("c.lat IS NOT NULL AND c.lng IS NOT NULL")
        clauses.append("c.lat BETWEEN ? AND ? AND c.lng BETWEEN ? AND ?")
        params += [spec.min_lat, spec.max_lat, spec.min_lng, spec.max_lng]
    return clauses, params


# deal_type → 거래 테이블 · select 컬럼. sale은 기존 transaction(회귀 0).
_TRADE_TABLE = {"sale": "transaction", "jeonse": "rent_transaction", "monthly": "rent_transaction"}
_SALE_COLS = "net_area, price, floor, deal_date, match_confidence"
_RENT_COLS = "net_area, deposit, monthly_rent, rent_type, floor, deal_date, match_confidence"


def _txn_where(spec: HardFilterSpec) -> tuple[list[str], list[object]]:
    """deal_type별 거래-where. sale=price / jeonse·monthly=rent_type+deposit(+monthly_rent).

    rent_type 제약은 rep·매칭·EXISTS 모두에 적용되나 SET 자체는 has_txn_filters가 결정
    (가격/면적 필터 없으면 EXISTS 미요구 → 전 단지, sale과 동일 의미).
    """
    clauses: list[str] = []
    params: list[object] = []
    if spec.net_area_min is not None:
        clauses.append("t.net_area >= ?")
        params.append(spec.net_area_min)
    if spec.net_area_max is not None:
        clauses.append("t.net_area <= ?")
        params.append(spec.net_area_max)
    if spec.deal_type == "sale":
        if spec.price_min is not None:
            clauses.append("t.price >= ?")
            params.append(spec.price_min)
        if spec.price_max is not None:
            clauses.append("t.price <= ?")
            params.append(spec.price_max)
    else:
        clauses.append("t.rent_type = ?")  # jeonse | monthly — 전월세 유형 한정
        params.append(spec.deal_type)
        if spec.deposit_min is not None:
            clauses.append("t.deposit >= ?")
            params.append(spec.deposit_min)
        if spec.deposit_max is not None:
            clauses.append("t.deposit <= ?")
            params.append(spec.deposit_max)
        if spec.deal_type == "monthly":
            if spec.monthly_rent_min is not None:
                clauses.append("t.monthly_rent >= ?")
                params.append(spec.monthly_rent_min)
            if spec.monthly_rent_max is not None:
                clauses.append("t.monthly_rent <= ?")
                params.append(spec.monthly_rent_max)
    if spec.deal_since is not None:
        clauses.append("t.deal_date >= ?")
        params.append(spec.deal_since.isoformat())
    return clauses, params


def _matching_trades(
    conn: sqlite3.Connection, spec: HardFilterSpec, complex_id: str,
    twhere: list[str], tparams: list[object],
) -> list[sqlite3.Row]:
    table = _TRADE_TABLE[spec.deal_type]
    cols = _SALE_COLS if spec.deal_type == "sale" else _RENT_COLS
    where = "t.complex_id = ?"
    params: list[object] = [complex_id]
    if twhere:
        where += " AND " + " AND ".join(twhere)
        params += tparams
    return conn.execute(
        f'SELECT {cols} FROM "{table}" t WHERE {where} ORDER BY t.deal_date DESC',
        params,
    ).fetchall()


def _build_rep(spec: HardFilterSpec, row: sqlite3.Row) -> RepresentativeTrade:
    """deal_type별 대표거래 — sale은 price, 전월세는 deposit/monthly_rent/rent_type."""
    if spec.deal_type == "sale":
        return RepresentativeTrade(
            net_area=row["net_area"], price=row["price"], floor=row["floor"],
            deal_date=row["deal_date"], match_confidence=row["match_confidence"],
        )
    return RepresentativeTrade(
        net_area=row["net_area"], price=None, deposit=row["deposit"],
        monthly_rent=row["monthly_rent"], rent_type=row["rent_type"], floor=row["floor"],
        deal_date=row["deal_date"], match_confidence=row["match_confidence"],
    )


def search_complexes(conn: sqlite3.Connection, spec: HardFilterSpec) -> list[Candidate]:
    """complex 속성+bbox 필터 → (txn 필터시) 매칭거래 EXISTS → 후보. 이진 in/out, limit."""
    cwhere, cparams = _complex_where(spec)
    twhere, tparams = _txn_where(spec)

    sql = (
        "SELECT c.complex_id, c.name, c.approval_date, c.parking_ratio, c.parking_underground, "
        "c.household_count, c.lat, c.lng, c.source_url FROM complex c"
    )
    trade_table = _TRADE_TABLE[spec.deal_type]
    # 가격축 집계 컬럼: 매매=price / 전월세=deposit. price_min/max에 deal_type 가격을 싣는다.
    amount_col = "price" if spec.deal_type == "sale" else "deposit"
    parts = list(cwhere)
    params = list(cparams)
    if spec.has_txn_filters:
        txn_cond = " AND " + " AND ".join(twhere)
        parts.append(
            f'EXISTS (SELECT 1 FROM "{trade_table}" t '
            f"WHERE t.complex_id = c.complex_id{txn_cond})"
        )
        params += tparams
    if parts:
        sql += " WHERE " + " AND ".join(parts)

    candidates: list[Candidate] = []
    for row in conn.execute(sql, params).fetchall():
        trades = _matching_trades(conn, spec, row["complex_id"], twhere, tparams)
        rep = trades[0] if trades else None
        amounts = [t[amount_col] for t in trades if t[amount_col] is not None]
        candidates.append(
            Candidate(
                complex_id=row["complex_id"],
                name=row["name"],
                approval_date=row["approval_date"],
                parking_ratio=row["parking_ratio"],
                parking_underground=row["parking_underground"],
                household_count=row["household_count"],
                lat=row["lat"],
                lng=row["lng"],
                source_url=row["source_url"],
                transaction_count=len(trades),
                price_min=min(amounts) if amounts else None,
                price_max=max(amounts) if amounts else None,
                representative_trade=_build_rep(spec, rep) if rep is not None else None,
            )
        )

    # 중립 정렬: 대표거래 최근일 desc(soft 점수 아님). 거래 없는 후보는 뒤로.
    candidates.sort(
        key=lambda c: c.representative_trade.deal_date or "" if c.representative_trade else "",
        reverse=True,
    )
    return candidates[: spec.limit]
