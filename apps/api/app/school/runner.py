"""학교 거리 근접 배치 코어 — eager 결정론·resumable. (school-1)

좌표 있는 complex × level(초/중/고) → SchoolIndex 인메모리 근접 → write_school(멱등).
**외부 API 0·쿼터 0·429 0**(정부 좌표 오프라인) → 단발 배치(poi식 멀티데이/우아중단 기계 불요).
이미 적재분 skip(resume·단지커밋). 좌표 read·school write만 → 지문/counts 불변.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from app.school.locations import LEVEL_ORDER, SchoolIndex
from app.school.store import done_levels, write_school


def pending_complexes(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """좌표 있고 아직 전 level 미적재인 complex(resume) — complex_id 정렬·limit."""
    return conn.execute(
        "SELECT c.complex_id, c.lat, c.lng FROM complex c "
        "WHERE c.lat IS NOT NULL AND c.lng IS NOT NULL AND "
        "(SELECT COUNT(*) FROM school_proximity s WHERE s.complex_id = c.complex_id) < ? "
        "ORDER BY c.complex_id LIMIT ?",
        (len(LEVEL_ORDER), limit),
    ).fetchall()


def enrich_school(
    conn: sqlite3.Connection,
    index: SchoolIndex,
    *,
    now: datetime,
    limit: int,
) -> dict[str, int]:
    """미적재 (단지×level)에 학교 근접 적재(인메모리 결정론). 멱등 resume·단지커밋.

    반환: {complexes, rows}. 외부 호출 0이라 quota/transient 개념 없음(전부 로컬 계산).
    """
    rows = pending_complexes(conn, limit)
    complexes = 0
    written = 0
    for row in rows:
        cid, lat, lng = row["complex_id"], row["lat"], row["lng"]
        done = done_levels(conn, cid)
        wrote = False
        for level in LEVEL_ORDER:
            if level in done:
                continue
            result = index.nearest(level, lat, lng)
            write_school(conn, cid, level, result, now=now)
            written += 1
            wrote = True
        if wrote:
            complexes += 1
            conn.commit()  # 단지 단위 커밋(resume-safe)
    return {"complexes": complexes, "rows": written}
