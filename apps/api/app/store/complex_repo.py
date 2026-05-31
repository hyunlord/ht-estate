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

from app.derive import has_gym, parking_ratio
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
