"""POI 근접 배치 코어 — resumable·quota-graceful. (poi-1)

좌표 있는 complex × 미적재 카테고리만 Kakao 1콜 → write_poi(멱등). 이미 적재분 skip(resume).
QuotaExceeded(429)면 우아 중단(쓴 만큼 보존·다음 run 이어받음). 좌표 read·poi write만 →
지문/counts 불변. CLI(scripts/enrich_poi.py)가 C47 공유 락·systemd로 감싼다. 키리스: client 주입.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime

from app.poi.proximity import (
    CATEGORIES,
    BadRequestError,
    KakaoLocalClient,
    QuotaExceeded,
    TransientError,
)
from app.poi.store import done_categories, write_poi, write_poi_skip

logger = logging.getLogger(__name__)


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
    transient_skips = 0
    bad_request_skips = 0
    for row in rows:
        cid, lat, lng = row["complex_id"], row["lat"], row["lng"]
        done = done_categories(conn, cid)
        wrote = False
        skipped = False
        for category, keyword in CATEGORIES:
            if category in done:
                continue
            try:
                result = client.search(category, keyword, x=lng, y=lat)  # Kakao x=lng,y=lat
            except QuotaExceeded:
                conn.commit()  # 쓴 만큼 보존
                return {
                    "complexes": complexes, "calls": calls,
                    "transient_skips": transient_skips,
                    "bad_request_skips": bad_request_skips, "quota_hit": True,
                }
            except TransientError:
                # 일시적 오류(재시도 소진) → 쓴 만큼 보존 + 이 단지 남은 카테고리 skip·다음 단지로.
                # 미완 카테고리는 done_categories로 다음 run retry(영구 갭 0, crash 0).
                conn.commit()
                transient_skips += 1
                skipped = True
                break
            except BadRequestError:
                # 진짜 per-row 400(quota 아님·체계적 4xx 아님) → 이 (단지,카테고리)만 skip 마킹하고
                # 같은 단지 나머지 카테고리는 계속(재시도 무의미·400은 재요청해도 안 변함). 마커가
                # done에 잡혀 다음 run에도 재호출 안 함(무한루프 0). search엔 안 보임(missing=KEEP).
                logger.warning(
                    "poi 400 bad-request skip — complex=%s category=%s x=%s y=%s",
                    cid, category, lng, lat,
                )
                write_poi_skip(conn, cid, category, now=now)
                bad_request_skips += 1
                wrote = True  # 마커 커밋 + 단지 진행으로 카운트(pending서 빠지게)
                continue
            write_poi(conn, cid, category, result, now=now)
            calls += 1
            wrote = True
            if interval:
                sleep(interval)
        if wrote and not skipped:
            complexes += 1
            conn.commit()  # 단지 단위 커밋(resume-safe)
    return {
        "complexes": complexes, "calls": calls,
        "transient_skips": transient_skips,
        "bad_request_skips": bad_request_skips, "quota_hit": False,
    }
