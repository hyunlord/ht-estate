"""전 세대타입 카탈로그 enrich (unit-type-catalog) — 비-아파트 대장 전유부 → 전용면적별 세대수.

소스 B: BldRgstHub 전유공용면적(getBrExposPubuseAreaInfo)의 호별 전유면적을 single-linkage 집계
→ (전용면적, 세대수) → unit_type(source='ledger_exclusive'). 비-아파트(rowhouse/officetel)만 —
아파트는 K-apt에 면적 엔드포인트 없고 대장↔지번 매핑 불안정(area_buckets 폴백).

규율(enrich_building_ledger 동형): **unit_type만 write**(좌표/canonical 무접촉 → 지문/counts 불변)·
resume-safe(정의적 결과를 ingest_progress stage='unit_type' 기록)·멱등·cron-lock·쿼터 백오프(429/캡
우아중단·다음 run resume). 멀티데이 백필(POI/ledger처럼 시간 두고 채움).

    uv run python scripts/enrich_unit_type.py --limit 300      # 이번 run 300건
    uv run python scripts/enrich_unit_type.py --sido 11 --limit 0  # 서울 비아파트 전량
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)
import httpx

# 비-아파트 타겟 선택·building_key 파싱은 ledger enrich와 동일(재사용·드리프트 0).
from enrich_building_ledger import _parse_key, nonapt_targets
from refill_kapt_fields import DEFAULT_INTER_BATCH_SLEEP, ShlockBatch, _chunks

from app.settings import get_api_key
from app.sources.building_ledger import cluster_areas, fetch_exclusive_areas, to_bun_ji
from app.sources.errors import PublicDataError
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db
from app.store.ledger_repo import ledger_source_url
from app.store.pipeline_state import bootstrap_pipeline_state_safe
from app.store.progress_repo import record_month
from app.store.regions import bjdong_code
from app.store.unit_type_repo import upsert_unit_types
from app.throttle import Throttle

UNIT_STAGE = "unit_type"
_UNIT_MONTH = "-"
_SOURCE = "ledger_exclusive"


def done_ids(conn: sqlite3.Connection) -> set[str]:
    """처리완료 complex_id(enriched + 정의적 skip) — 재개 skip(stage='unit_type')."""
    rows = conn.execute(
        "SELECT region FROM ingest_progress WHERE stage = ?", (UNIT_STAGE,)
    ).fetchall()
    return {r[0] for r in rows}


def enrich_one(
    conn: sqlite3.Connection,
    complex_id: str,
    *,
    api_key: str,
    now: datetime,
    client: httpx.Client | None = None,
) -> str:
    """한 건물 전유부 → unit_type. enriched | no_jibun | no_bjdong | no_units(전부 정의적·기록)."""
    parsed = _parse_key(complex_id)
    if parsed is None:
        return "no_jibun"
    sgg, dong, jibun = parsed
    bjdong = bjdong_code(sgg, dong)
    if bjdong is None:
        return "no_bjdong"
    bunji = to_bun_ji(jibun)
    if bunji is None:
        return "no_jibun"
    bun, ji = bunji
    areas = fetch_exclusive_areas(sgg, bjdong, bun, ji, api_key=api_key, client=client)
    buckets = cluster_areas(areas)
    if not buckets:
        return "no_units"
    upsert_unit_types(
        conn, complex_id, buckets, source=_SOURCE,
        source_url=ledger_source_url(sgg, bjdong, bun, ji), fetched_at=now,
    )
    return "enriched"


def run_enrich(
    conn: sqlite3.Connection,
    *,
    api_key: str,
    lock: Callable[[], object],
    throttle: Throttle | None,
    batch_size: int,
    limit: int,
    sido_prefixes: list[str] | None = None,
    inter_batch_sleep: float = DEFAULT_INTER_BATCH_SLEEP,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] | None = None,
) -> Counter[str]:
    """미완료 비-아파트를 배치 단위로 전유부 enrich. 배치마다 공유락·정의적 결과만 기록(재개)·쿼터
    우아중단. **unit_type만 write**(좌표 불변)."""
    done = done_ids(conn)
    pending = [cid for cid, _ in nonapt_targets(conn, sido_prefixes) if cid not in done]
    if limit > 0:
        pending = pending[:limit]
    if log is not None:
        log(f"unit_type 대상 {len(pending)}건 (완료 {len(done)} skip, batch={batch_size})")
    now = datetime.now(UTC)
    counts: Counter[str] = Counter()
    batches = list(_chunks(pending, batch_size))
    for bi, batch in enumerate(batches):
        with lock() as acquired:  # type: ignore[attr-defined]
            if not acquired:
                if log is not None:
                    log("공유락 점유 중(cron) — 이번 run 양보, 다음 run 재개")
                break
            for complex_id in batch:
                if throttle is not None:
                    throttle.wait()
                try:
                    outcome = enrich_one(
                        conn, complex_id, api_key=api_key, now=now, client=client
                    )
                except (PublicDataError, httpx.HTTPError) as exc:
                    if log is not None:
                        log(
                            f"공공API 오류({type(exc).__name__}) — 캡/레이트리밋/네트워크 추정, "
                            "이번 run 중단(진행 기록으로 재개·완료분 보존)"
                        )
                    conn.commit()
                    return counts
                record_month(conn, UNIT_STAGE, complex_id, _UNIT_MONTH, 1)  # 정의적 → 기록
                counts[outcome] += 1
            conn.commit()
        if inter_batch_sleep > 0 and bi < len(batches) - 1:
            sleep(inter_batch_sleep)
        if log is not None and sum(counts.values()) % 200 == 0 and counts:
            log(f"  …처리 {sum(counts.values())}건 (enriched={counts['enriched']})")
    if log is not None:
        log(
            f"unit_type 완료 — 이번 run {sum(counts.values())}건: enriched={counts['enriched']} "
            f"no_units={counts['no_units']} no_bjdong={counts['no_bjdong']} "
            f"no_jibun={counts['no_jibun']}"
        )
    return counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="enrich_unit_type", description="전 세대타입(비아파트 전유부)")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--lock", default=None, help="공유 락(기본 <db디렉토리>/.ingest.lock)")
    p.add_argument("--interval", type=float, default=0.3, help="API 호출 간 최소 간격(초)")
    p.add_argument("--limit", type=int, default=0, help="이번 run 최대 건수(0=무제한)")
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--max-spin", type=float, default=60.0)
    p.add_argument("--inter-batch-sleep", type=float, default=DEFAULT_INTER_BATCH_SLEEP)
    p.add_argument("--sido", default="", help="sgg 앞2(시도) 콤마목록 — 부분 enrich(예: 11,26)")
    args = p.parse_args(argv)
    sido_prefixes = [s.strip() for s in args.sido.split(",") if s.strip()] or None

    conn = get_connection(args.db)
    init_db(conn)
    lock_path = args.lock or str(Path(args.db).resolve().parent / ".ingest.lock")
    throttle = Throttle(args.interval) if args.interval > 0 else None
    lock = ShlockBatch(lock_path, max_spin=args.max_spin)
    run_enrich(
        conn, api_key=get_api_key(), lock=lock, throttle=throttle,
        batch_size=args.batch_size, limit=args.limit, sido_prefixes=sido_prefixes,
        inter_batch_sleep=args.inter_batch_sleep, log=print,
    )
    bootstrap_pipeline_state_safe(conn)  # pipeline-state: run-end 자기서술(META만)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
