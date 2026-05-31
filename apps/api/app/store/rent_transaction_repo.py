"""rent_transaction 적재 — MOLIT RentTrade → 결정론 txn_id → 멱등 upsert → 월 증분 (P2-1).

매매(transaction_repo)와 동형이되 가격축만 다름: deposit(보증금)+monthly_rent(월세)+rent_type
(전세=월세0). 퍼지조인(complex_id·match_confidence)은 join_repo 재사용 — 적재 시 NULL로 두고
재적재 시 보존(조인 결과 불변). provenance: source_url 없음(MOLIT 암묵), updated_at만.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime

import httpx

from app.match.jibun import from_molit, to_canonical
from app.sources.molit_rent import RentTrade, fetch_rent_trades
from app.throttle import Throttle

_INSERT_COLUMNS = (
    "txn_id",
    "apt_name_raw",
    "legal_dong",
    "bjd_code",
    "jibun",
    "road_addr",
    "build_year",
    "net_area",
    "deposit",
    "monthly_rent",
    "rent_type",
    "contract_type",
    "floor",
    "deal_date",
    "updated_at",
)
# 재적재 시 갱신할 컬럼(complex_id·match_confidence 제외 = 조인 결과 보존)
_UPDATE_COLUMNS = tuple(c for c in _INSERT_COLUMNS if c != "txn_id")


def make_rent_txn_id(trade: RentTrade) -> str:
    """RentTrade 식별 필드로 결정론 해시. 보증금·월세·계약구분을 포함(매매와 키 공간 분리)."""
    parts = [
        trade.sgg_cd or "",
        trade.jibun or "",
        trade.apt_name,
        trade.legal_dong,
        f"{trade.net_area:.2f}",
        str(trade.floor),
        trade.deal_date.isoformat(),
        str(trade.deposit),
        str(trade.monthly_rent),
        trade.contract_type or "",
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def upsert_rent_transaction(
    conn: sqlite3.Connection,
    trade: RentTrade,
    *,
    updated_at: datetime | None = None,
) -> str:
    """RentTrade → rent_transaction 행 upsert(멱등). txn_id 반환. 조인 컬럼은 NULL 유지/보존."""
    when = (updated_at or datetime.now(UTC)).isoformat()
    txn_id = make_rent_txn_id(trade)
    values = {
        "txn_id": txn_id,
        "apt_name_raw": trade.apt_name,
        "legal_dong": trade.legal_dong,
        "bjd_code": trade.bjd_code,
        "jibun": to_canonical(from_molit(trade.bonbun, trade.bubun, trade.jibun)),
        "road_addr": trade.road_addr,
        "build_year": trade.build_year,
        "net_area": trade.net_area,
        "deposit": trade.deposit,
        "monthly_rent": trade.monthly_rent,
        "rent_type": trade.rent_type,
        "contract_type": trade.contract_type,
        "floor": trade.floor,
        "deal_date": trade.deal_date.isoformat(),
        "updated_at": when,
    }
    columns = ", ".join(_INSERT_COLUMNS)
    placeholders = ", ".join(f":{c}" for c in _INSERT_COLUMNS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in _UPDATE_COLUMNS)
    conn.execute(
        f"INSERT INTO rent_transaction ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(txn_id) DO UPDATE SET {updates}",
        values,
    )
    return txn_id


def ingest_rent_month(
    conn: sqlite3.Connection,
    lawd_cd: str,
    deal_ym: str,
    *,
    api_key: str,
    client: httpx.Client | None = None,
    updated_at: datetime | None = None,
) -> int:
    """한 지역×월의 전월세 전 페이지를 적재. 적재 행 수 반환. 멱등(에러 시 부분적재 없음)."""
    when = updated_at or datetime.now(UTC)
    trades = fetch_rent_trades(lawd_cd, deal_ym, api_key=api_key, client=client)
    for trade in trades:
        upsert_rent_transaction(conn, trade, updated_at=when)
    conn.commit()
    return len(trades)


def ingest_rent_months(
    conn: sqlite3.Connection,
    lawd_cd: str,
    deal_yms: Iterable[str],
    *,
    api_key: str,
    throttle: Throttle | None = None,
    client: httpx.Client | None = None,
    updated_at: datetime | None = None,
) -> int:
    """여러 월을 throttle 끼워 순차 적재. 총 적재 행 수 반환(매매 ingest_months와 동형)."""
    total = 0
    for deal_ym in deal_yms:
        if throttle is not None:
            throttle.wait()
        total += ingest_rent_month(
            conn, lawd_cd, deal_ym, api_key=api_key, client=client, updated_at=updated_at
        )
    return total
