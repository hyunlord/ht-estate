"""POI 근접 배치 코어 — resumable·quota-graceful. (poi-1)

좌표 있는 complex × 미적재 카테고리만 Kakao 1콜 → write_poi(멱등). 이미 적재분 skip(resume).
QuotaExceeded(429)면 우아 중단(쓴 만큼 보존·다음 run 이어받음). 좌표 read·poi write만 →
지문/counts 불변. CLI(scripts/enrich_poi.py)가 C47 공유 락·systemd로 감싼다. 키리스: client 주입.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from datetime import datetime

from app.poi.proximity import CATEGORIES, KakaoLocalClient, QuotaExceeded
from app.poi.store import done_categories, write_poi


def pending_complexes(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """좌표 있고 아직 전 카테고리 미적재인 complex(resume) — complex_id 정렬·limit."""
    return conn.execute(
        "SELECT c.complex_id, c.lat, c.lng FROM complex c "
        "WHERE c.lat IS NOT NULL AND c.lng IS NOT NULL AND "
        "(SELECT COUNT(*) FROM poi_proximity p WHERE p.complex_id = c.complex_id) < ? "
        "ORDER BY c.complex_id LIMIT ?",
        (len(CATEGORIES), limit),
    ).fetchall()


def enrich_poi(
    conn: sqlite3.Connection,
    client: KakaoLocalClient,
    *,
    now: datetime,
    limit: int,
    interval: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int | bool]:
    """미적재 (단지×카테고리)에 Kakao 근접 적재. 멱등 resume·quota-graceful.

    반환: {complexes, calls, quota_hit}. quota_hit이면 다음 run이 이어받음(쓴 만큼 보존).
    """
    rows = pending_complexes(conn, limit)
    complexes = 0
    calls = 0
    for row in rows:
        cid, lat, lng = row["complex_id"], row["lat"], row["lng"]
        done = done_categories(conn, cid)
        wrote = False
        for category, keyword in CATEGORIES:
            if category in done:
                continue
            try:
                result = client.search(category, keyword, x=lng, y=lat)  # Kakao x=lng,y=lat
            except QuotaExceeded:
                conn.commit()  # 쓴 만큼 보존
                return {"complexes": complexes, "calls": calls, "quota_hit": True}
            write_poi(conn, cid, category, result, now=now)
            calls += 1
            wrote = True
            if interval:
                sleep(interval)
        if wrote:
            complexes += 1
            conn.commit()  # 단지 단위 커밋(resume-safe)
    return {"complexes": complexes, "calls": calls, "quota_hit": False}
