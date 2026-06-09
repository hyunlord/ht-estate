"""POI 근접 배치 CLI (poi-1) — Kakao Local 거리계산, resumable·quota-graceful·C47 공존.

좌표 있는 complex × 미적재 카테고리만 Kakao 1콜 → poi_proximity(멱등). 청크 단위로 공유
`.ingest.lock`(C47) spin-acquire/release(거래·enrich cron과 직렬화·굶김 방지). 429(C48 동형)면
우아 중단(쓴 만큼 보존·다음 run resume). **좌표 read·poi write만** → 지문/counts 불변.

키 필요(.env KAKAO_REST_API_KEY)라 키리스 게이트 밖(ops). 코어/테스트는 app/poi/runner.py.

    uv run python scripts/enrich_poi.py --limit 2000 --chunk 25 --interval 0.2
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)
from refill_kapt_fields import ShlockBatch

import app.settings  # noqa: F401  (.env 로딩)
from app.poi.proximity import client_from_env
from app.poi.runner import enrich_poi
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="enrich_poi")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--limit", type=int, default=2000, help="이번 run 처리할 complex 상한")
    p.add_argument("--chunk", type=int, default=25, help="락 보유당 complex 수(배치)")
    p.add_argument("--interval", type=float, default=0.2, help="Kakao 콜 간 sleep(초)")
    p.add_argument("--inter-batch-sleep", type=float, default=2.0, help="청크 사이 release 창(초)")
    p.add_argument("--lock", default=None, help="공유 락(기본 <db디렉토리>/.ingest.lock)")
    p.add_argument("--max-spin", type=float, default=60.0)
    args = p.parse_args(argv)

    client = client_from_env(os.environ.get("KAKAO_REST_API_KEY", ""))
    if client is None:
        print("✗ KAKAO_REST_API_KEY 미설정 — 중단")
        return 1

    conn = get_connection(args.db)
    init_db(conn)
    lock_path = args.lock or str(Path(args.db).resolve().parent / ".ingest.lock")
    lock = ShlockBatch(lock_path, max_spin=args.max_spin)

    remaining = args.limit
    total_c = total_calls = 0
    while remaining > 0:
        with lock() as acquired:  # type: ignore[attr-defined]
            if not acquired:
                print("[skip] cron 장시간 점유 — 이번 run 양보(resume)")
                break
            r = enrich_poi(
                conn, client, now=datetime.now(UTC),
                limit=min(args.chunk, remaining), interval=args.interval,
            )
        total_c += int(r["complexes"])
        total_calls += int(r["calls"])
        remaining -= int(r["complexes"])
        if r["quota_hit"]:
            print(f"[quota] Kakao 429 우아중단. complexes={total_c} calls={total_calls}")
            return 0
        if int(r["complexes"]) == 0:
            print("[done] 더 처리할 미적재 complex 없음(또는 청크 0).")
            break
        time.sleep(args.inter_batch_sleep)  # cron release 창
    print(f"[ok] complexes={total_c} calls={total_calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
