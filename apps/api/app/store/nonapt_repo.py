"""비-아파트(연립·오피스텔) 전월세 적재 — 거래에서 건물 도출(1안) + rent_transaction 연결. (P5-1b-2)

아파트는 K-apt(마스터)↔MOLIT 퍼지조인이지만, 비-아파트는 마스터가 없어 **거래 레코드에서 건물을
도출**한다(같은 레코드 → 퍼지조인 불필요, complex_id 직접 부여).

**building_key = PNU식** (STEP 1: roadNm 없음 → 도로명 키 폐기):
    {property_type[:2]} : sgg_cd : 법정동명 : 정규화 지번 : 정규화 건물명
한 지번 다건물을 건물명으로 디스앰비그. 건축물대장(2안)이 같은 (법정동코드+지번) PNU로 조인 가능.

건물 = complex 행(property_type ∈ rowhouse/officetel, 얇은 속성: name·주소·approval(연식)).
K-apt 전용 컬럼(has_gym·parking_*·household_count 등)은 NULL(없는 게 정상). 거래 = rent_transaction
재사용(complex_id=도출 건물, match_confidence=1.0 — 같은 레코드라 확정). geocode·markers·search·
property_type 필터는 기존 경로가 자동 처리.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime

import httpx

from app.match.jibun import from_molit, to_canonical
from app.match.normalize import normalize_name
from app.sources.molit_nonapt import NonAptKind, NonAptRentTrade, fetch_nonapt_rent
from app.throttle import Throttle


def building_key(trade: NonAptRentTrade) -> str:
    """거래 → 결정론 PNU식 건물 키(complex_id). 같은 건물의 여러 거래는 같은 키(멱등)."""
    jibun_c = to_canonical(from_molit(None, None, trade.jibun)) or "?"
    name_n = normalize_name(trade.name) or "?"
    parts = [trade.property_type[:2], trade.sgg_cd or "?", trade.legal_dong, jibun_c, name_n]
    return ":".join(parts)


def _geocodable_addr(trade: NonAptRentTrade) -> str:
    """geocode용 지번 주소 — 시군구명(Offi)+법정동+지번. backfill_coords가 road_addr로 처리."""
    parts = [trade.sgg_nm or "", trade.legal_dong, trade.jibun or ""]
    return " ".join(p for p in parts if p).strip()


# complex(건물) upsert — geo(lat/lng/geo_*)는 컬럼 미포함 → 충돌 시 보존(영구 캐시 불변).
_BUILDING_COLS = (
    "complex_id", "name", "property_type", "legal_addr", "road_addr", "approval_date", "updated_at"
)


def upsert_nonapt_building(
    conn: sqlite3.Connection, trade: NonAptRentTrade, *, updated_at: datetime | None = None
) -> str:
    """도출 건물 → complex 행 upsert(멱등). building_key 반환. lat/lng/geo는 보존(미갱신)."""
    when = (updated_at or datetime.now(UTC)).isoformat()
    key = building_key(trade)
    addr = _geocodable_addr(trade)
    approval = f"{trade.build_year}-01-01" if trade.build_year else None  # 연식 근사(신축용)
    values = {
        "complex_id": key,
        "name": trade.name,
        "property_type": trade.property_type,
        "legal_addr": addr,
        "road_addr": addr,  # roadNm 없음 → 지번 주소로 geocode(Kakao 주소검색 지번 처리)
        "approval_date": approval,
        "updated_at": when,
    }
    cols = ", ".join(_BUILDING_COLS)
    ph = ", ".join(f":{c}" for c in _BUILDING_COLS)
    upd = ", ".join(f"{c} = excluded.{c}" for c in _BUILDING_COLS if c != "complex_id")
    conn.execute(
        f"INSERT INTO complex ({cols}) VALUES ({ph}) ON CONFLICT(complex_id) DO UPDATE SET {upd}",
        values,
    )
    return key


def make_nonapt_rent_txn_id(trade: NonAptRentTrade) -> str:
    """비-아파트 전월세 결정론 해시(건물키 + 면적·층·일자·금액). 멱등 키."""
    parts = [
        building_key(trade),
        f"{trade.net_area:.2f}",
        str(trade.floor),
        trade.deal_date.isoformat(),
        str(trade.deposit),
        str(trade.monthly_rent),
        trade.contract_type or "",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


_RENT_COLS = (
    "txn_id", "complex_id", "match_confidence", "apt_name_raw", "legal_dong", "sgg_cd",
    "jibun", "build_year", "net_area", "deposit", "monthly_rent", "rent_type",
    "contract_type", "floor", "deal_date", "updated_at",
)


def upsert_nonapt_rent(
    conn: sqlite3.Connection, trade: NonAptRentTrade, *, updated_at: datetime | None = None
) -> str:
    """비-아파트 전월세 → rent_transaction upsert. complex_id=도출 건물(확정, 퍼지조인 불필요)."""
    when = (updated_at or datetime.now(UTC)).isoformat()
    txn_id = make_nonapt_rent_txn_id(trade)
    values = {
        "txn_id": txn_id,
        "complex_id": building_key(trade),  # 같은 레코드 도출 → 확정 연결
        "match_confidence": 1.0,
        "apt_name_raw": trade.name,
        "legal_dong": trade.legal_dong,
        "sgg_cd": trade.sgg_cd,
        "jibun": to_canonical(from_molit(None, None, trade.jibun)),
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
    cols = ", ".join(_RENT_COLS)
    ph = ", ".join(f":{c}" for c in _RENT_COLS)
    upd = ", ".join(f"{c} = excluded.{c}" for c in _RENT_COLS if c != "txn_id")
    conn.execute(
        f"INSERT INTO rent_transaction ({cols}) VALUES ({ph}) "
        f"ON CONFLICT(txn_id) DO UPDATE SET {upd}",
        values,
    )
    return txn_id


def ingest_nonapt_rent_month(
    conn: sqlite3.Connection,
    lawd_cd: str,
    deal_ym: str,
    *,
    kind: NonAptKind,
    api_key: str,
    client: httpx.Client | None = None,
    updated_at: datetime | None = None,
) -> int:
    """한 지역×월×kind의 비-아파트 전월세 적재 — 건물 도출 + 거래 연결. 적재 행수 반환(멱등)."""
    when = updated_at or datetime.now(UTC)
    trades = fetch_nonapt_rent(lawd_cd, deal_ym, kind=kind, api_key=api_key, client=client)
    for trade in trades:
        upsert_nonapt_building(conn, trade, updated_at=when)
        upsert_nonapt_rent(conn, trade, updated_at=when)
    conn.commit()
    return len(trades)


def ingest_nonapt_rent_months(
    conn: sqlite3.Connection,
    lawd_cd: str,
    deal_yms: Iterable[str],
    *,
    kind: NonAptKind,
    api_key: str,
    throttle: Throttle | None = None,
    client: httpx.Client | None = None,
    updated_at: datetime | None = None,
) -> int:
    """여러 월을 throttle 끼워 순차 적재. 총 행수 반환(아파트 ingest_rent_months와 동형)."""
    total = 0
    for deal_ym in deal_yms:
        if throttle is not None:
            throttle.wait()
        total += ingest_nonapt_rent_month(
            conn, lawd_cd, deal_ym, kind=kind, api_key=api_key, client=client, updated_at=updated_at
        )
    return total
