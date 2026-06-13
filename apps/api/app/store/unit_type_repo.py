"""unit_type 적재 — 전 세대타입 카탈로그(전용면적별 세대수·거래 무관).

enrich는 **이 테이블만 write**(complex 좌표·complex/txn/rent canonical 무접촉) → 지문/counts 불변.
멱등 UPSERT((단지,면적,소스) PK)·DELETE/TRUNCATE 0(누적·wipe 위험 0)·provenance.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any


def upsert_unit_types(
    conn: sqlite3.Connection,
    complex_id: str,
    buckets: list[tuple[float, int]],
    *,
    source: str,
    source_url: str | None,
    fetched_at: datetime,
) -> int:
    """buckets=[(net_area, household_count)] → unit_type UPSERT(멱등). 충돌 시 세대수/provenance
    갱신. **이 테이블만 write** — 좌표/canonical 무접촉. 적재 버킷 수 반환."""
    if not buckets:
        return 0
    conn.executemany(
        "INSERT INTO unit_type "
        "(complex_id, net_area, household_count, source, source_url, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(complex_id, net_area, source) DO UPDATE SET "
        "  household_count = excluded.household_count, "
        "  source_url = excluded.source_url, "
        "  fetched_at = excluded.fetched_at",
        [
            (complex_id, na, hh, source, source_url, fetched_at.isoformat())
            for na, hh in buckets
        ],
    )
    conn.commit()
    return len(buckets)


def unit_types_for(conn: sqlite3.Connection, complex_id: str) -> list[dict[str, Any]]:
    """단지의 전 세대타입(면적순) — 디테일 병합용 read-only."""
    rows = conn.execute(
        "SELECT net_area, household_count, source, source_url FROM unit_type "
        "WHERE complex_id = ? ORDER BY net_area",
        (complex_id,),
    ).fetchall()
    return [
        {"net_area": r[0], "household_count": r[1], "source": r[2], "source_url": r[3]}
        for r in rows
    ]
