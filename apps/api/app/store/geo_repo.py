"""complex 좌표 백필 — road_addr를 좌표DB로 매칭해 lat/lng 채움 (영구 캐시).

멱등: lat이 이미 있으면 skip(영구 캐시 — 좌표는 정적, 설계 §8). road_addr만 갱신
대상이 아니므로 적재(T0-2)와 독립. provenance: geo_source(DB명+기준일)·geo_updated_at.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.geo.match import match_coord


def backfill_coords(
    conn: sqlite3.Connection,
    coord_index: dict[str, tuple[float, float]],
    *,
    geo_source: str,
    updated_at: datetime | None = None,
) -> dict[str, int]:
    """lat이 NULL인 단지를 좌표DB로 매칭해 lat/lng + provenance 채움. 멱등.

    {matched, unmatched, total} 반환. 무매치는 lat NULL로 남긴다(억지 추정 안 함).
    """
    when = (updated_at or datetime.now(UTC)).isoformat()
    pending = conn.execute(
        "SELECT complex_id, road_addr FROM complex WHERE lat IS NULL AND road_addr IS NOT NULL"
    ).fetchall()

    matched = 0
    for row in pending:
        coord = match_coord(row["road_addr"], coord_index)
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
