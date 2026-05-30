"""complex 좌표 백필 — road_addr를 실시간 geocode해 lat/lng 채움 (영구 캐시).

멱등: lat이 이미 있으면 skip(영구 캐시 — 좌표는 정적, 설계 §8). road_addr는 갱신
대상이 아니므로 적재(T0-2)와 독립. provenance: geo_source(geocoder명)·geo_updated_at.
geocode는 (road_addr)->(lat,lng)|None 콜러블(주입) — 테스트는 가짜/MockTransport로.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from app.throttle import Throttle

Geocode = Callable[[str], tuple[float, float] | None]


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
