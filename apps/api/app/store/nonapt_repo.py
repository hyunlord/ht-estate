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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.match.jibun import from_molit, to_canonical
from app.match.normalize import normalize_name
from app.sources.molit_nonapt import (
    NonAptKind,
    NonAptRentTrade,
    NonAptSaleTrade,
    fetch_nonapt_rent,
    fetch_nonapt_sale,
)
from app.store.regions import sigungu_label
from app.throttle import Throttle


class BuildingTradeLike(Protocol):
    """building_key·geocode·건물 upsert가 읽는 거래 필드(구조적·duck-typed).

    NonAptTradeBase·DerivedAptTrade가 구조적으로 충족. property_type[:2]가 키 접두 —
    rowhouse→ro·officetel→of·apartment→ap(K-apt 단지코드[콜론 없음]와 구분).
    """

    # read-only property로 선언 — 공변(covariant). 가변 속성 protocol은 불변이라 NonAptKind(Literal)
    # 같은 str 서브타입이 비할당됨 → property로 회피(NonAptTradeBase·DerivedAptTrade 충족).
    @property
    def property_type(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def legal_dong(self) -> str: ...
    @property
    def jibun(self) -> str | None: ...
    @property
    def sgg_cd(self) -> str | None: ...
    @property
    def sgg_nm(self) -> str | None: ...
    @property
    def build_year(self) -> int | None: ...


def building_key(trade: BuildingTradeLike) -> str:
    """거래 → 결정론 PNU식 건물 키(complex_id). 같은 건물의 여러 거래는 같은 키(멱등)."""
    jibun_c = to_canonical(from_molit(None, None, trade.jibun)) or "?"
    name_n = normalize_name(trade.name) or "?"
    parts = [trade.property_type[:2], trade.sgg_cd or "?", trade.legal_dong, jibun_c, name_n]
    return ":".join(parts)


def _geocodable_addr(trade: BuildingTradeLike) -> str:
    """geocode용 지번 주소 — '시도 시군구 법정동 지번'. backfill_coords가 road_addr로 처리.

    roadNm이 없어 '법정동 지번'만이면 동/구명 전국 중복 시 Kakao가 타도시로 오지오코딩한다
    (부산 중구 영주동 → 경북 영주시). 시군구코드로 '시도 시군구'를 앞에 붙여 해소(RH는 sggNm이
    없어 코드 룩업 필수). 미매핑 시 sggNm(Offi)→공백 순 fallback(기존 동작 보존).
    """
    label = sigungu_label(trade.sgg_cd) or trade.sgg_nm or ""
    parts = [label, trade.legal_dong, trade.jibun or ""]
    return " ".join(p for p in parts if p).strip()


# complex(건물) upsert — geo(lat/lng/geo_*)는 컬럼 미포함 → 충돌 시 보존(영구 캐시 불변).
_BUILDING_COLS = (
    "complex_id", "name", "property_type", "legal_addr", "road_addr", "approval_date", "updated_at"
)


def upsert_nonapt_building(
    conn: sqlite3.Connection, trade: BuildingTradeLike, *, updated_at: datetime | None = None
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


# building-add(#6-③B): orphan 아파트 거래(transaction/rent_transaction 행)를 미등재 건물로 도출.
# K-apt fuzzy join 미스(complex_id NULL)를 nonapt식 결정론 키로 건물화 — property_type='apartment'·
# complex_id 'ap:' 접두(K-apt 단지코드[콜론 없음]·ro:/of:와 구분). 지문 re-baseline이 K-apt 서브셋
# 무드리프트를 ap: 제외로 증명 가능. building_key/upsert_nonapt_building을 그대로 재사용(동형).
@dataclass(frozen=True)
class DerivedAptTrade:
    """orphan 아파트 거래 → 건물 도출 trade(BuildingTradeLike 충족). 결손이면 도출불가(천장)."""

    name: str
    legal_dong: str
    jibun: str | None
    sgg_cd: str | None
    build_year: int | None
    property_type: str = "apartment"
    sgg_nm: str | None = None

    @classmethod
    def from_txn_row(cls, row: sqlite3.Row) -> DerivedAptTrade:
        """txn/rent_transaction 행(apt_name_raw·bjd_code·legal_dong·jibun·build_year) → trade."""
        bjd = row["bjd_code"]
        return cls(
            name=row["apt_name_raw"] or "",
            legal_dong=row["legal_dong"] or "",
            jibun=row["jibun"],
            sgg_cd=bjd[:5] if bjd and len(bjd) >= 5 else None,
            build_year=row["build_year"],
        )


def is_derivable_apt(row: sqlite3.Row) -> bool:
    """orphan 행이 비-degenerate 건물키 산출 가능한가(jibun 캐논·정규화명·sgg 모두 존재).

    도출불가(전월세 jibun/bjd 결손 등)는 NULL 유지 — 억지 생성 금지(missing=keep·천장 수용).
    """
    if not (row["apt_name_raw"] and normalize_name(row["apt_name_raw"])):
        return False
    if not (row["bjd_code"] and len(row["bjd_code"]) >= 5):
        return False
    return to_canonical(from_molit(None, None, row["jibun"])) is not None


def upsert_apartment_building(
    conn: sqlite3.Connection, trade: DerivedAptTrade, *, updated_at: datetime | None = None
) -> str:
    """도출 아파트 건물 → thin complex 행 upsert(geo 보존·멱등). upsert_nonapt_building 재사용."""
    return upsert_nonapt_building(conn, trade, updated_at=updated_at)


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


# ─────────────────────────── 매매(P5-1b-3) ───────────────────────────
# 전월세와 같은 building_key로 같은 건물에 합류(좌표 재사용). 매매축은 transaction 테이블에
# 적재 — complex_id=도출 건물(확정·match_confidence 1.0). 아파트 매매와 동일 테이블/스키마.


def make_nonapt_sale_txn_id(trade: NonAptSaleTrade) -> str:
    """비-아파트 매매 결정론 해시(건물키 + 면적·층·일자·금액). 멱등 키."""
    parts = [
        building_key(trade),
        f"{trade.net_area:.2f}",
        str(trade.floor),
        trade.deal_date.isoformat(),
        str(trade.price),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


# transaction 컬럼 — nonapt는 bjd_code/road_addr 없음(NULL). complex_id·match_confidence는
# 도출이 확정이라 INSERT/UPDATE 모두 채운다(아파트 매매는 T0-4 조인이라 NULL 두는 것과 다름).
_SALE_COLS = (
    "txn_id", "complex_id", "match_confidence", "apt_name_raw", "legal_dong",
    "jibun", "build_year", "net_area", "price", "floor", "deal_date", "updated_at",
)


def upsert_nonapt_sale(
    conn: sqlite3.Connection, trade: NonAptSaleTrade, *, updated_at: datetime | None = None
) -> str:
    """비-아파트 매매 → transaction upsert(멱등). complex_id=도출 건물(확정, 퍼지조인 불필요)."""
    when = (updated_at or datetime.now(UTC)).isoformat()
    txn_id = make_nonapt_sale_txn_id(trade)
    values = {
        "txn_id": txn_id,
        "complex_id": building_key(trade),  # 같은 레코드 도출 → 확정 연결
        "match_confidence": 1.0,
        "apt_name_raw": trade.name,
        "legal_dong": trade.legal_dong,
        "jibun": to_canonical(from_molit(None, None, trade.jibun)),
        "build_year": trade.build_year,
        "net_area": trade.net_area,
        "price": trade.price,
        "floor": trade.floor,
        "deal_date": trade.deal_date.isoformat(),
        "updated_at": when,
    }
    cols = ", ".join(_SALE_COLS)
    ph = ", ".join(f":{c}" for c in _SALE_COLS)
    upd = ", ".join(f"{c} = excluded.{c}" for c in _SALE_COLS if c != "txn_id")
    conn.execute(
        f'INSERT INTO "transaction" ({cols}) VALUES ({ph}) '
        f"ON CONFLICT(txn_id) DO UPDATE SET {upd}",
        values,
    )
    return txn_id


def ingest_nonapt_sale_month(
    conn: sqlite3.Connection,
    lawd_cd: str,
    deal_ym: str,
    *,
    kind: NonAptKind,
    api_key: str,
    client: httpx.Client | None = None,
    updated_at: datetime | None = None,
) -> int:
    """한 지역×월×kind 비-아파트 매매 적재 — 건물 도출 + transaction 연결. 행수(멱등·취소 제외)."""
    when = updated_at or datetime.now(UTC)
    trades = fetch_nonapt_sale(lawd_cd, deal_ym, kind=kind, api_key=api_key, client=client)
    for trade in trades:
        upsert_nonapt_building(conn, trade, updated_at=when)
        upsert_nonapt_sale(conn, trade, updated_at=when)
    conn.commit()
    return len(trades)


def ingest_nonapt_sale_months(
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
    """여러 월을 throttle 끼워 순차 적재. 총 행수 반환(전월세 동형)."""
    total = 0
    for deal_ym in deal_yms:
        if throttle is not None:
            throttle.wait()
        total += ingest_nonapt_sale_month(
            conn, lawd_cd, deal_ym, kind=kind, api_key=api_key, client=client, updated_at=updated_at
        )
    return total
