"""school_proximity store — write-back + resume done-set + 검색 attach read. (school-1)

좌표 read·school_proximity write만 → 지문/counts 불변. (단지,level) PK upsert(멱등 resume).
poi_proximity와 동형이나 소스/의미 다름(정부 학교좌표 거리 vs Kakao POI) → 별개 테이블.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from app.school.locations import LEVEL_LABELS, SOURCE, NearestResult


class SchoolNear(BaseModel):
    """카드/필터용 한 level 근접 요약. computed-or-dash(미적재면 이 level 행 자체가 없음)."""

    level: str  # elem|mid|high
    label: str  # 초등학교|중학교|고등학교
    nearest_dist_m: int | None
    nearest_name: str | None
    nearest_school_id: str | None
    count_500m: int | None
    count_1km: int | None


def write_school(
    conn: sqlite3.Connection,
    complex_id: str,
    level: str,
    result: NearestResult,
    *,
    now: datetime,
    source: str = SOURCE,
) -> None:
    """(단지,level) upsert(멱등). 갱신(반기 재계산) 시 거리/개수/시각 덮어쓴다."""
    conn.execute(
        "INSERT INTO school_proximity "
        "(complex_id, level, nearest_dist_m, nearest_name, nearest_school_id, "
        " count_500m, count_1km, fetched_at, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(complex_id, level) DO UPDATE SET "
        "nearest_dist_m=excluded.nearest_dist_m, nearest_name=excluded.nearest_name, "
        "nearest_school_id=excluded.nearest_school_id, count_500m=excluded.count_500m, "
        "count_1km=excluded.count_1km, fetched_at=excluded.fetched_at, source=excluded.source",
        (
            complex_id, level, result.nearest_dist_m, result.nearest_name,
            result.nearest_school_id, result.count_500m, result.count_1km, now.isoformat(), source,
        ),
    )


def done_levels(conn: sqlite3.Connection, complex_id: str) -> set[str]:
    """이미 적재된 level(resume skip용)."""
    rows = conn.execute(
        "SELECT level FROM school_proximity WHERE complex_id = ?", (complex_id,)
    ).fetchall()
    return {r["level"] for r in rows}


class _SchoolTarget(Protocol):
    complex_id: str
    school: list[SchoolNear] | None


def attach_school(conn: sqlite3.Connection, candidates: Sequence[_SchoolTarget]) -> None:
    """후보들에 school_proximity 근접을 in-place 부착(읽기 전용·computed-or-dash 빈 리스트)."""
    if not candidates:
        return
    summaries = read_school(conn, [c.complex_id for c in candidates])
    for cand in candidates:
        cand.school = summaries.get(cand.complex_id, [])


def read_school(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, list[SchoolNear]]:
    """후보 id들의 school_proximity → {id: [SchoolNear...]}. 미적재 단지는 빈 리스트."""
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT complex_id, level, nearest_dist_m, nearest_name, nearest_school_id, "
        f"count_500m, count_1km FROM school_proximity WHERE complex_id IN ({ph}) "
        "ORDER BY complex_id, level",
        list(ids),
    ).fetchall()
    out: dict[str, list[SchoolNear]] = {cid: [] for cid in ids}
    for r in rows:
        lvl = str(r["level"])
        out[r["complex_id"]].append(
            SchoolNear(
                level=lvl,
                label=LEVEL_LABELS.get(lvl, lvl),
                nearest_dist_m=r["nearest_dist_m"],
                nearest_name=r["nearest_name"],
                nearest_school_id=r["nearest_school_id"],
                count_500m=r["count_500m"],
                count_1km=r["count_1km"],
            )
        )
    return out
