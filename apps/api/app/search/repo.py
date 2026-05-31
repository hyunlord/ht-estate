"""search_complexes — HardFilterSpec → complex ⨝ transaction → 후보(이진 in/out).

설계 §7: hard 조건은 이진 in/out. soft 점수·랭킹 없음(중립 정렬: 대표거래 최근일 desc).
저신뢰 매칭은 제외하지 않고 match_confidence를 후보에 실어 "추정 매칭" 배지를 가능케 한다(§5.1).
gym은 어디에도 없다(R1 — Tier-2 소관).
"""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel

from app.search.gym import GymSummary
from app.search.spec import HardFilterSpec


class RepresentativeTrade(BaseModel):
    """범위 내 최근 1건 — 카드의 실거래 줄."""

    net_area: float | None
    price: int | None  # 만원
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
    # Tier-2 gym(R1: hard filter 아님 — 후보 산출 후 attach_gym으로 부착). repo는 안 채움.
    gym: GymSummary | None = None


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


def _txn_where(spec: HardFilterSpec) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if spec.net_area_min is not None:
        clauses.append("t.net_area >= ?")
        params.append(spec.net_area_min)
    if spec.net_area_max is not None:
        clauses.append("t.net_area <= ?")
        params.append(spec.net_area_max)
    if spec.price_min is not None:
        clauses.append("t.price >= ?")
        params.append(spec.price_min)
    if spec.price_max is not None:
        clauses.append("t.price <= ?")
        params.append(spec.price_max)
    if spec.deal_since is not None:
        clauses.append("t.deal_date >= ?")
        params.append(spec.deal_since.isoformat())
    return clauses, params


def _matching_trades(
    conn: sqlite3.Connection, complex_id: str, twhere: list[str], tparams: list[object]
) -> list[sqlite3.Row]:
    where = "t.complex_id = ?"
    params: list[object] = [complex_id]
    if twhere:
        where += " AND " + " AND ".join(twhere)
        params += tparams
    return conn.execute(
        f"SELECT net_area, price, floor, deal_date, match_confidence "
        f'FROM "transaction" t WHERE {where} ORDER BY t.deal_date DESC',
        params,
    ).fetchall()


def search_complexes(conn: sqlite3.Connection, spec: HardFilterSpec) -> list[Candidate]:
    """complex 속성+bbox 필터 → (txn 필터시) 매칭거래 EXISTS → 후보. 이진 in/out, limit."""
    cwhere, cparams = _complex_where(spec)
    twhere, tparams = _txn_where(spec)

    sql = (
        "SELECT c.complex_id, c.name, c.approval_date, c.parking_ratio, c.parking_underground, "
        "c.household_count, c.lat, c.lng, c.source_url FROM complex c"
    )
    parts = list(cwhere)
    params = list(cparams)
    if spec.has_txn_filters:
        txn_cond = " AND " + " AND ".join(twhere)
        parts.append(
            f'EXISTS (SELECT 1 FROM "transaction" t '
            f"WHERE t.complex_id = c.complex_id{txn_cond})"
        )
        params += tparams
    if parts:
        sql += " WHERE " + " AND ".join(parts)

    candidates: list[Candidate] = []
    for row in conn.execute(sql, params).fetchall():
        trades = _matching_trades(conn, row["complex_id"], twhere, tparams)
        rep = trades[0] if trades else None
        prices = [t["price"] for t in trades if t["price"] is not None]
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
                price_min=min(prices) if prices else None,
                price_max=max(prices) if prices else None,
                representative_trade=RepresentativeTrade(
                    net_area=rep["net_area"],
                    price=rep["price"],
                    floor=rep["floor"],
                    deal_date=rep["deal_date"],
                    match_confidence=rep["match_confidence"],
                )
                if rep is not None
                else None,
            )
        )

    # 중립 정렬: 대표거래 최근일 desc(soft 점수 아님). 거래 없는 후보는 뒤로.
    candidates.sort(
        key=lambda c: c.representative_trade.deal_date or "" if c.representative_trade else "",
        reverse=True,
    )
    return candidates[: spec.limit]
