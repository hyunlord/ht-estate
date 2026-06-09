"""poi_proximity store — write-back + resume done-set + 검색 attach read. (poi-1)

좌표 read·poi_proximity write만 → 지문/counts 불변. (단지,카테고리) PK upsert(멱등 resume).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from app.poi.proximity import CATEGORY_LABELS, PoiResult


class PoiNear(BaseModel):
    """카드/필터용 한 카테고리 근접 요약. computed-or-dash(미적재면 이 카테고리 행 자체가 없음)."""

    category: str
    label: str
    nearest_dist_m: int | None
    nearest_name: str | None
    count_500m: int | None
    count_1km: int | None


def write_poi(
    conn: sqlite3.Connection,
    complex_id: str,
    category: str,
    result: PoiResult,
    *,
    now: datetime,
    source: str = "kakao_local",
) -> None:
    """(단지,카테고리) upsert(멱등). 갱신 시 거리/개수/시각 덮어쓴다."""
    conn.execute(
        "INSERT INTO poi_proximity "
        "(complex_id, category, nearest_dist_m, nearest_name, count_500m, count_1km, "
        " fetched_at, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(complex_id, category) DO UPDATE SET "
        "nearest_dist_m=excluded.nearest_dist_m, nearest_name=excluded.nearest_name, "
        "count_500m=excluded.count_500m, count_1km=excluded.count_1km, "
        "fetched_at=excluded.fetched_at, source=excluded.source",
        (
            complex_id, category, result.nearest_dist_m, result.nearest_name,
            result.count_500m, result.count_1km, now.isoformat(), source,
        ),
    )


def done_categories(conn: sqlite3.Connection, complex_id: str) -> set[str]:
    """이미 적재된 카테고리(resume skip용)."""
    rows = conn.execute(
        "SELECT category FROM poi_proximity WHERE complex_id = ?", (complex_id,)
    ).fetchall()
    return {r["category"] for r in rows}


class _PoiTarget(Protocol):
    """attach_poi 대상 — complex_id 읽기 + poi 쓰기 가능한 후보(repo.Candidate)."""

    complex_id: str
    poi: list[PoiNear] | None


def attach_poi(conn: sqlite3.Connection, candidates: Sequence[_PoiTarget]) -> None:
    """후보들에 poi_proximity 근접을 in-place 부착(읽기 전용 · computed-or-dash 빈 리스트)."""
    if not candidates:
        return
    summaries = read_poi(conn, [c.complex_id for c in candidates])
    for cand in candidates:
        cand.poi = summaries.get(cand.complex_id, [])


def read_poi(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, list[PoiNear]]:
    """후보 id들의 poi_proximity → {id: [PoiNear...]}. 미적재 단지는 빈 리스트(computed-or-dash)."""
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT complex_id, category, nearest_dist_m, nearest_name, count_500m, count_1km "
        f"FROM poi_proximity WHERE complex_id IN ({ph}) ORDER BY complex_id, category",
        list(ids),
    ).fetchall()
    out: dict[str, list[PoiNear]] = {cid: [] for cid in ids}
    for r in rows:
        cat = str(r["category"])
        out[r["complex_id"]].append(
            PoiNear(
                category=cat,
                label=CATEGORY_LABELS.get(cat, cat),
                nearest_dist_m=r["nearest_dist_m"],
                nearest_name=r["nearest_name"],
                count_500m=r["count_500m"],
                count_1km=r["count_1km"],
            )
        )
    return out
