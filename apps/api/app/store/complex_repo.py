"""complex 테이블 적재 — ComplexInfo + 파생 → 행 upsert (provenance 포함).

provenance(원칙3): source_url·updated_at을 여기서부터 채운다. source_url엔 절대
serviceKey를 넣지 않는다(K-apt 단지 페이지 딥링크, secretless).
멱등: complex_id 충돌 시 갱신(ON CONFLICT UPDATE) — 재적재 안전.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from app.derive import (
    has_daycare,
    has_gym,
    has_library,
    has_playground,
    has_senior_center,
    parking_ratio,
)
from app.sources.kapt import ComplexInfo, fetch_complex_info, list_complexes
from app.throttle import Throttle

# K-apt 단지 기본정보 페이지(사람 방문용 딥링크, secretless). UI(T0-7)에서 확정 가능.
KAPT_DETAIL_URL = "https://www.k-apt.go.kr/kaptinfo/kaptinfobasis.do?kaptCode={code}"

_COLUMNS = (
    "complex_id",
    "name",
    "bjd_code",
    "legal_addr",
    "road_addr",
    "approval_date",
    "household_count",
    "building_type",
    "corridor_type",
    "parking_total",
    "parking_ground",
    "parking_underground",
    "parking_ratio",
    "amenities_raw",
    "has_gym",
    # ── P4-1 풀필드 (raw) ──
    "heat_type",
    "sale_type",
    "mgmt_type",
    "dong_count",
    "top_floor",
    "priv_area",
    "mgmt_area",
    "builder",
    "developer",
    "mgmt_staff",
    "security_type",
    "security_staff",
    "cleaning_type",
    "cleaning_staff",
    "disinfection_type",
    "disinfection_staff",
    "disinfection_method",
    "garbage_type",
    "water_supply",
    "electricity_contract",
    "fire_alarm",
    "internet",
    "elevator_count",
    "cctv_count",
    "subway_line",
    "subway_station",
    "subway_time",
    "bus_time",
    "convenient_facility_raw",
    "education_facility_raw",
    # ── P4-1 파생(welfare 토큰) ──
    "has_daycare",
    "has_playground",
    "has_senior_center",
    "has_library",
    "updated_at",
    "source_url",
)


def upsert_complex(
    conn: sqlite3.Connection,
    info: ComplexInfo,
    *,
    updated_at: datetime | None = None,
    source_url: str | None = None,
) -> None:
    """ComplexInfo → complex 행 적재(파생 has_gym·parking_ratio 포함). 멱등 upsert.

    provenance: source_url(기본 K-apt 단지 페이지)·updated_at(기본 now, UTC).
    """
    when = (updated_at or datetime.now(UTC)).isoformat()
    src = source_url or KAPT_DETAIL_URL.format(code=info.kapt_code)
    ratio = parking_ratio(info.parking_total, info.household_count)

    values = {
        "complex_id": info.kapt_code,
        "name": info.name,
        "bjd_code": info.bjd_code,
        "legal_addr": info.legal_addr,
        "road_addr": info.road_addr,
        "approval_date": info.approval_date.isoformat() if info.approval_date else None,
        "household_count": info.household_count,
        "building_type": info.building_type,
        "corridor_type": info.corridor_type,
        "parking_total": info.parking_total,
        "parking_ground": info.parking_ground,
        "parking_underground": info.parking_underground,
        "parking_ratio": ratio,
        "amenities_raw": info.amenities_raw,
        "has_gym": 1 if has_gym(info.amenities_raw) else 0,
        # ── P4-1 풀필드 (raw — ComplexInfo에서 그대로) ──
        "heat_type": info.heat_type,
        "sale_type": info.sale_type,
        "mgmt_type": info.mgmt_type,
        "dong_count": info.dong_count,
        "top_floor": info.top_floor,
        "priv_area": info.priv_area,
        "mgmt_area": info.mgmt_area,
        "builder": info.builder,
        "developer": info.developer,
        "mgmt_staff": info.mgmt_staff,
        "security_type": info.security_type,
        "security_staff": info.security_staff,
        "cleaning_type": info.cleaning_type,
        "cleaning_staff": info.cleaning_staff,
        "disinfection_type": info.disinfection_type,
        "disinfection_staff": info.disinfection_staff,
        "disinfection_method": info.disinfection_method,
        "garbage_type": info.garbage_type,
        "water_supply": info.water_supply,
        "electricity_contract": info.electricity_contract,
        "fire_alarm": info.fire_alarm,
        "internet": info.internet,
        "elevator_count": info.elevator_count,
        "cctv_count": info.cctv_count,
        "subway_line": info.subway_line,
        "subway_station": info.subway_station,
        "subway_time": info.subway_time,
        "bus_time": info.bus_time,
        "convenient_facility_raw": info.convenient_facility_raw,
        "education_facility_raw": info.education_facility_raw,
        # ── P4-1 파생(welfare 토큰 — amenities_raw에서) ──
        "has_daycare": 1 if has_daycare(info.amenities_raw) else 0,
        "has_playground": 1 if has_playground(info.amenities_raw) else 0,
        "has_senior_center": 1 if has_senior_center(info.amenities_raw) else 0,
        "has_library": 1 if has_library(info.amenities_raw) else 0,
        "updated_at": when,
        "source_url": src,
    }

    placeholders = ", ".join(f":{col}" for col in _COLUMNS)
    columns = ", ".join(_COLUMNS)
    # complex_id 제외 전 컬럼을 갱신 대상으로 (멱등 재적재)
    updates = ", ".join(f"{col} = excluded.{col}" for col in _COLUMNS if col != "complex_id")
    conn.execute(
        f"INSERT INTO complex ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(complex_id) DO UPDATE SET {updates}",
        values,
    )
    conn.commit()


def ingest_complexes(
    conn: sqlite3.Connection,
    *,
    region: str,
    api_key: str,
    client: httpx.Client | None = None,
    throttle: Throttle | None = None,
    log: Callable[[str], None] | None = None,
) -> int:
    """시군구 region의 단지목록 → 각 단지정보 → upsert_complex(파생 포함). 적재 단지수 반환.

    멱등(upsert): 재실행 안전. throttle.wait()를 각 단지정보 호출 직전에 호출(쿼터).
    파생 has_gym·parking_ratio는 upsert_complex 내부에서 계산된다(T0-2).
    """
    refs = list_complexes(api_key=api_key, sigungu=region, client=client)
    total = len(refs)
    count = 0
    for index, ref in enumerate(refs):
        if throttle is not None:
            throttle.wait()
        info = fetch_complex_info(ref.kapt_code, api_key=api_key, client=client)
        if info is None:
            continue
        upsert_complex(conn, info)
        count += 1
        if log is not None:
            log(f"단지 {index + 1}/{total} 적재 ({ref.name})")
    return count
