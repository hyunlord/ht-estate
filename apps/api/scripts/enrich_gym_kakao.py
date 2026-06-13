"""Kakao Local 헬스장 신호 enrich (gym-kakao) — 비아파트 단지 ≤50m 헬스장 → gym ✓ 있음(네이버 없이).

단지 좌표 근처 Kakao Local "헬스장/피트니스"(≤50m=건물내/동일지) → gym enrichment 사실
(source_type='kakao_local'·고신뢰)로 write_facts. synthesize_gym이 최고-confidence primary로 →
디테일 ✓ 있음(C80 게이트)·soft gym 점수 포함. **missing=keep**: 무매치면 write 0(단정 "없음" 아님).

규율: gym enrichment 사실만 write(좌표/complex/txn/rent 무접촉 → 지문/counts 불변)·멱등(write_facts
upsert + progress skip)·resume(stage='gym_kakao')·Kakao 쿼터 우아중단(C48)·일시오류 skip+continue.
비아파트 집중(아파트는 K-apt amenities). 멀티데이 백필(POI처럼). **네이버 금지(Kakao Local만).**

    uv run python scripts/enrich_gym_kakao.py --limit 200      # 이번 run 200건
    uv run python scripts/enrich_gym_kakao.py --sido 11 --limit 0  # 서울 비아파트 전량
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import _bootstrap  # noqa: F401
from refill_kapt_fields import DEFAULT_INTER_BATCH_SLEEP, ShlockBatch, _chunks

from app.enrich.store import EnrichmentFact, write_facts
from app.poi.proximity import (
    BadRequestError,
    QuotaExceeded,
    TransientError,
    client_from_env,
)
from app.search.gym import ATTRIBUTE as GYM_ATTRIBUTE
from app.search.gym_kakao import GYM_CONFIDENCE, SOURCE_TYPE, gym_fact_value, nearest_gym
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db
from app.store.pipeline_state import bootstrap_pipeline_state_safe
from app.store.progress_repo import record_month

GYM_STAGE = "gym_kakao"
_GYM_MONTH = "-"
_TTL = timedelta(days=365)  # 정적 물리 POI — 장수명(재-run이 멱등 갱신·progress-skip로 재조회 0)


def done_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT region FROM ingest_progress WHERE stage = ?", (GYM_STAGE,)
    ).fetchall()
    return {r[0] for r in rows}


def targets(
    conn: sqlite3.Connection, sido_prefixes: list[str] | None
) -> list[tuple[str, float, float]]:
    """비-아파트(좌표 보유) — 아파트는 K-apt amenities로 gym 있음. sido_prefixes면 그 시도만."""
    sql = (
        "SELECT complex_id, lat, lng FROM complex "
        "WHERE property_type IN ('rowhouse','officetel') AND lat IS NOT NULL AND lng IS NOT NULL"
    )
    params: list[object] = []
    if sido_prefixes:
        ph = ",".join("?" * len(sido_prefixes))
        sql += f" AND substr(complex_id, 4, 2) IN ({ph})"
        params = list(sido_prefixes)
    sql += " ORDER BY complex_id"
    return [(r[0], r[1], r[2]) for r in conn.execute(sql, params).fetchall()]


def run(
    conn: sqlite3.Connection,
    client,  # type: ignore[no-untyped-def]
    *,
    lock: Callable[[], object],
    batch_size: int,
    limit: int,
    sido_prefixes: list[str] | None = None,
    inter_batch_sleep: float = DEFAULT_INTER_BATCH_SLEEP,
    interval: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] | None = None,
) -> Counter[str]:
    """비-아파트 배치 Kakao gym 검색 → 매치면 사실 write(아니면 skip·missing=keep)."""
    done = done_ids(conn)
    coords = {cid: (lat, lng) for cid, lat, lng in targets(conn, sido_prefixes) if cid not in done}
    pending = list(coords)  # list[str] — _chunks 타입 정합
    if limit > 0:
        pending = pending[:limit]
    if log is not None:
        log(f"gym-kakao 대상 {len(pending)}건 (완료 {len(done)} skip, batch={batch_size})")
    now = datetime.now(UTC)
    counts: Counter[str] = Counter()
    for batch in _chunks(pending, batch_size):
        with lock() as acquired:  # type: ignore[attr-defined]
            if not acquired:
                if log is not None:
                    log("공유락 점유 중(cron) — 이번 run 양보, 다음 run 재개")
                break
            for cid in batch:
                lat, lng = coords[cid]
                if interval > 0:
                    sleep(interval)
                try:
                    match = nearest_gym(client, lat, lng)
                except QuotaExceeded:
                    if log is not None:
                        log("Kakao 일쿼터 초과 — 우아중단(다음 run resume·완료분 보존)")
                    conn.commit()
                    return counts
                except TransientError:
                    counts["transient_skip"] += 1  # 재기록 안 함 → 다음 run 재시도
                    continue
                except BadRequestError:
                    counts["bad_skip"] += 1
                    record_month(conn, GYM_STAGE, cid, _GYM_MONTH, 1)  # per-row 영구 → 기록
                    continue
                if match is not None:
                    write_facts(
                        conn, cid, GYM_ATTRIBUTE,
                        [EnrichmentFact(
                            value=gym_fact_value(match), confidence=GYM_CONFIDENCE,
                            source_type=SOURCE_TYPE,
                            source_url=match["url"] or f"kakao:gym:{match['place_name']}",
                        )],
                        ttl=_TTL, now=now,
                    )
                    counts["matched"] += 1
                else:
                    counts["no_match"] += 1
                record_month(conn, GYM_STAGE, cid, _GYM_MONTH, 1)  # 처리완료 → resume skip
            conn.commit()
        if inter_batch_sleep > 0:
            sleep(inter_batch_sleep)
        if log is not None and sum(counts.values()) % 200 == 0 and counts:
            log(f"  …처리 {sum(counts.values())}건 (matched={counts['matched']})")
    if log is not None:
        log(
            f"gym-kakao 완료 — 이번 run {sum(counts.values())}건: matched={counts['matched']} "
            f"no_match={counts['no_match']} transient={counts['transient_skip']}"
        )
    return counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="enrich_gym_kakao", description="Kakao 헬스장 신호(비아파트)")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--lock", default=None)
    p.add_argument("--interval", type=float, default=0.1, help="Kakao 콜 간 sleep(초)")
    p.add_argument("--limit", type=int, default=0, help="이번 run 최대 건수(0=무제한)")
    p.add_argument("--batch-size", type=int, default=25)
    p.add_argument("--max-spin", type=float, default=60.0)
    p.add_argument("--inter-batch-sleep", type=float, default=DEFAULT_INTER_BATCH_SLEEP)
    p.add_argument("--sido", default="", help="sgg 앞2(시도) 콤마목록")
    args = p.parse_args(argv)
    sido_prefixes = [s.strip() for s in args.sido.split(",") if s.strip()] or None

    client = client_from_env(os.environ.get("KAKAO_REST_API_KEY", ""))
    if client is None:
        print("✗ KAKAO_REST_API_KEY 미설정 — 중단")
        return 1

    conn = get_connection(args.db)
    init_db(conn)
    lock_path = args.lock or str(Path(args.db).resolve().parent / ".ingest.lock")
    lock = ShlockBatch(lock_path, max_spin=args.max_spin)
    run(
        conn, client, lock=lock, batch_size=args.batch_size, limit=args.limit,
        sido_prefixes=sido_prefixes, inter_batch_sleep=args.inter_batch_sleep,
        interval=args.interval, log=print,
    )
    bootstrap_pipeline_state_safe(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
