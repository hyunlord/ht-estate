"""complex 좌표 백필 — road_addr를 실시간 geocode해 lat/lng 채움 (영구 캐시).

멱등: lat이 이미 있으면 skip(영구 캐시 — 좌표는 정적, 설계 §8). road_addr는 갱신
대상이 아니므로 적재(T0-2)와 독립. provenance: geo_source(geocoder명)·geo_updated_at.
geocode는 (road_addr)->(lat,lng)|None 콜러블(주입) — 테스트는 가짜/MockTransport로.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from app.throttle import Throttle

Geocode = Callable[[str], tuple[float, float] | None]

# 비-아파트 도출 건물(바운드 geocode 대상). 아파트는 backfill_coords(공유)가 처리 — 무접촉.
_NONAPT_TYPES = ("rowhouse", "officetel")
# 도심 우선 — 메트로가 nonapt 건물의 ~90%(서울·경기·인천·부산…) → 핵심 좌표를 먼저 확보.
# 시도코드(complex_id `pt:sgg:…`의 sgg 앞2)로 정렬. GLOB '??:NN*'로 안정 추출(substr 비의존).
_CITY_FIRST = ("11", "41", "28", "26", "27", "30", "29", "31", "36")


def _city_first_order() -> str:
    cases = " ".join(
        f"WHEN complex_id GLOB '??:{code}*' THEN {i}" for i, code in enumerate(_CITY_FIRST)
    )
    return f"CASE {cases} ELSE {len(_CITY_FIRST)} END, complex_id"


def geocode_nonapt_pending(
    conn: sqlite3.Connection,
    geocode: Geocode,
    *,
    limit: int,
    geo_source: str,
    throttle: Throttle | None = None,
    updated_at: datetime | None = None,
) -> dict[str, int]:
    """lat-NULL 비-아파트 건물을 **도심우선·LIMIT개** geocode(증분커밋·cap-graceful).

    `backfill_coords`(아파트 포함 글로벌·1회커밋)와 달리, 멀티데이 백필을 청크로 끊기 위한 바운드
    버전: property_type ∈ {rowhouse, officetel}만, 도심우선 정렬, LIMIT개만, 50건마다 커밋(중단 시
    유실 최소·resume). Kakao 한도/HTTP 에러면 우아하게 중단(`stopped=1`). 아파트는 대상 제외 →
    lat/lng 무접촉(공유 backfill_coords가 담당).

    {geocoded, considered, remaining, stopped} 반환(stopped: 1=Kakao 에러 중단, 0=정상).
    """
    when = (updated_at or datetime.now(UTC)).isoformat()
    types_ph = ",".join("?" * len(_NONAPT_TYPES))
    rows = conn.execute(
        f"SELECT complex_id, road_addr FROM complex "
        f"WHERE lat IS NULL AND road_addr IS NOT NULL AND property_type IN ({types_ph}) "
        f"ORDER BY {_city_first_order()} LIMIT ?",
        (*_NONAPT_TYPES, limit),
    ).fetchall()

    geocoded = 0
    stopped = 0
    try:
        for row in rows:
            if throttle is not None:
                throttle.wait()
            coord = geocode(row["road_addr"])
            if coord is None:
                continue  # 무결과 → lat NULL 유지(다음 패스 재시도)
            lat, lng = coord
            conn.execute(
                "UPDATE complex SET lat = ?, lng = ?, geo_source = ?, geo_updated_at = ? "
                "WHERE complex_id = ?",
                (lat, lng, geo_source, when, row["complex_id"]),
            )
            geocoded += 1
            if geocoded % 50 == 0:
                conn.commit()  # 증분(중단 시 유실 최소)
    except httpx.HTTPError:
        stopped = 1  # Kakao 한도/HTTP — 우아하게 중단(커밋 후 resume)
    conn.commit()

    remaining = conn.execute(
        f"SELECT COUNT(*) FROM complex WHERE lat IS NULL AND road_addr IS NOT NULL "
        f"AND property_type IN ({types_ph})",
        _NONAPT_TYPES,
    ).fetchone()[0]
    return {
        "geocoded": geocoded,
        "considered": len(rows),
        "remaining": remaining,
        "stopped": stopped,
    }


def backfill_coords(
    conn: sqlite3.Connection,
    geocode: Geocode,
    *,
    geo_source: str,
    throttle: Throttle | None = None,
    updated_at: datetime | None = None,
) -> dict[str, int]:
    """lat NULL인 단지를 geocode해 lat/lng + provenance 채움. 멱등(있으면 skip).

    {matched, unmatched, total} 반환. 무결과는 lat NULL로 남긴다(억지 추정 안 함).
    throttle.wait()를 각 geocode 직전에 호출해 쿼터 초과를 막는다.
    """
    when = (updated_at or datetime.now(UTC)).isoformat()
    pending = conn.execute(
        "SELECT complex_id, road_addr FROM complex WHERE lat IS NULL AND road_addr IS NOT NULL"
    ).fetchall()

    matched = 0
    for row in pending:
        if throttle is not None:
            throttle.wait()
        coord = geocode(row["road_addr"])
        if coord is None:
            continue
        lat, lng = coord
        conn.execute(
            "UPDATE complex SET lat = ?, lng = ?, geo_source = ?, geo_updated_at = ? "
            "WHERE complex_id = ?",
            (lat, lng, geo_source, when, row["complex_id"]),
        )
        matched += 1

    conn.commit()
    return {"matched": matched, "unmatched": len(pending) - matched, "total": len(pending)}
