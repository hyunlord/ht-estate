"""transaction 테이블 적재 — MOLIT Trade → 결정론 txn_id → 멱등 upsert → 월 증분.

퍼지 조인(complex_id 채우기·match_confidence)은 T0-4 소관이라 적재 시 NULL로 둔다.
재적재 시에도 **complex_id·match_confidence는 건드리지 않는다**(T0-4 조인 결과 보존).

provenance(원칙3): transaction엔 source_url 없음(§4 — 출처는 MOLIT 암묵). updated_at만.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime

import httpx

from app.sources.molit import Trade, fetch_trades
from app.throttle import Throttle

# MOLIT 소스 필드 + provenance. complex_id/match_confidence는 INSERT 시 NULL,
# UPDATE 시 갱신 대상에서 제외(아래 _UPDATE_COLUMNS).
_INSERT_COLUMNS = (
    "txn_id",
    "apt_name_raw",
    "legal_dong",
    "bjd_code",
    "road_addr",
    "build_year",
    "net_area",
    "price",
    "floor",
    "deal_date",
    "updated_at",
)
# 재적재 시 갱신할 컬럼(complex_id·match_confidence 제외 = 조인 결과 보존)
_UPDATE_COLUMNS = tuple(c for c in _INSERT_COLUMNS if c != "txn_id")


def make_txn_id(trade: Trade) -> str:
    """Trade 식별 필드로 결정론 해시 txn_id. 같은 거래→같은 id, 다른 거래→다른 id.

    호(unit) 정보가 MOLIT에 없어 같은 단지·면적·층·일·금액 2건은 같은 id로 dedup된다
    (희박, 수용). float 면적은 고정포맷으로 정규화해 repr 흔들림을 제거한다.
    """
    parts = [
        trade.sgg_cd or "",
        trade.apt_seq or "",
        trade.jibun or "",
        trade.apt_name,
        trade.legal_dong,
        f"{trade.net_area:.2f}",
        str(trade.floor),
        trade.deal_date.isoformat(),
        str(trade.price),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def upsert_transaction(
    conn: sqlite3.Connection,
    trade: Trade,
    *,
    updated_at: datetime | None = None,
) -> str:
    """Trade → transaction 행 upsert(멱등). txn_id 반환. complex_id/match_confidence는 NULL 유지.

    재적재 시 MOLIT 소스 필드+updated_at만 갱신, 조인 컬럼은 보존.
    """
    when = (updated_at or datetime.now(UTC)).isoformat()
    txn_id = make_txn_id(trade)
    values = {
        "txn_id": txn_id,
        "apt_name_raw": trade.apt_name,
        "legal_dong": trade.legal_dong,
        "bjd_code": trade.bjd_code,
        "road_addr": trade.road_addr,
        "build_year": trade.build_year,
        "net_area": trade.net_area,
        "price": trade.price,
        "floor": trade.floor,
        "deal_date": trade.deal_date.isoformat(),
        "updated_at": when,
    }
    columns = ", ".join(_INSERT_COLUMNS)
    placeholders = ", ".join(f":{c}" for c in _INSERT_COLUMNS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in _UPDATE_COLUMNS)
    conn.execute(
        f'INSERT INTO "transaction" ({columns}) VALUES ({placeholders}) '
        f"ON CONFLICT(txn_id) DO UPDATE SET {updates}",
        values,
    )
    return txn_id


def ingest_month(
    conn: sqlite3.Connection,
    lawd_cd: str,
    deal_ym: str,
    *,
    api_key: str,
    client: httpx.Client | None = None,
    updated_at: datetime | None = None,
) -> int:
    """한 지역×월의 전 페이지 실거래를 적재. 적재된 행 수 반환. 재실행 멱등.

    fetch는 전 페이지를 모은 뒤 한 트랜잭션으로 커밋(에러 시 부분적재 없음). 빈 응답이면 0.
    에러 응답은 `PublicDataError`로 전파(graceful — 절반 쓰지 않음).
    """
    when = updated_at or datetime.now(UTC)
    trades = fetch_trades(lawd_cd, deal_ym, api_key=api_key, client=client)
    for trade in trades:
        upsert_transaction(conn, trade, updated_at=when)
    conn.commit()
    return len(trades)


def ingest_months(
    conn: sqlite3.Connection,
    lawd_cd: str,
    deal_yms: Iterable[str],
    *,
    api_key: str,
    throttle: Throttle | None = None,
    client: httpx.Client | None = None,
    updated_at: datetime | None = None,
) -> int:
    """여러 월을 throttle를 끼워 순차 적재. 총 적재 행 수 반환.

    throttle.wait()를 각 월 fetch 직전에 호출해 개발계정 일일한도 초과를 막는다.
    """
    total = 0
    for deal_ym in deal_yms:
        if throttle is not None:
            throttle.wait()
        total += ingest_month(
            conn, lawd_cd, deal_ym, api_key=api_key, client=client, updated_at=updated_at
        )
    return total
